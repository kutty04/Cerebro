import os
import re
import time
import socket
import signal
import sys
import subprocess
import ipaddress
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional, Tuple, Set, List, Dict
import logging
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


@dataclass
class IngestionLimits:
    MAX_REPO_CLONE_TIMEOUT_SEC: int = int(os.getenv("MAX_REPO_CLONE_TIMEOUT_SEC", "60"))
    MAX_INGESTION_TIMEOUT_SEC: int = int(os.getenv("MAX_INGESTION_TIMEOUT_SEC", "120"))
    MAX_REPO_DISK_SIZE_BYTES: int = int(os.getenv("MAX_REPO_DISK_SIZE_BYTES", str(50 * 1024 * 1024)))  # 50MB
    MAX_REPO_FILES_SCANNED: int = int(os.getenv("MAX_REPO_FILES_SCANNED", "500"))
    MAX_REPO_FILES_INDEXED: int = int(os.getenv("MAX_REPO_FILES_INDEXED", "200"))
    MAX_FILE_SIZE_BYTES: int = int(os.getenv("MAX_FILE_SIZE_BYTES", str(500 * 1024)))  # 500KB
    MAX_TOTAL_INDEXED_BYTES: int = int(os.getenv("MAX_TOTAL_INDEXED_BYTES", str(10 * 1024 * 1024)))  # 10MB
    MAX_TOTAL_CHUNKS: int = int(os.getenv("MAX_TOTAL_CHUNKS", "1000"))
    MAX_PATH_LENGTH: int = int(os.getenv("MAX_PATH_LENGTH", "250"))
    MAX_REPO_URL_LENGTH: int = int(os.getenv("MAX_REPO_URL_LENGTH", "500"))
    MAX_CONCURRENT_JOBS: int = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))
    # Rate Limits
    MAX_USER_INGESTIONS_PER_WINDOW: int = int(os.getenv("MAX_USER_INGESTIONS_PER_WINDOW", "5"))
    USER_WINDOW_SECONDS: int = int(os.getenv("USER_WINDOW_SECONDS", "3600"))  # 1 hour
    MAX_GLOBAL_INGESTIONS_PER_WINDOW: int = int(os.getenv("MAX_GLOBAL_INGESTIONS_PER_WINDOW", "20"))
    GLOBAL_WINDOW_SECONDS: int = int(os.getenv("GLOBAL_WINDOW_SECONDS", "3600"))  # 1 hour
    MAX_RATE_LIMITER_MAP_SIZE: int = int(os.getenv("MAX_RATE_LIMITER_MAP_SIZE", "10000"))


class IngestionConcurrencyManager:
    def __init__(self, limits: IngestionLimits):
        self.limits = limits
        self.active_jobs: Set[Tuple[str, str]] = set()

    def acquire(self, user_id: str, repo_url: str):
        if len(self.active_jobs) >= self.limits.MAX_CONCURRENT_JOBS:
            logger.warning("Ingestion capacity reached [op=acquire_job, active_count=%d]", len(self.active_jobs))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Ingestion capacity reached. Please try again later.",
            )

        job_key = (user_id, repo_url)
        if job_key in self.active_jobs:
            logger.warning("Duplicate active ingestion job rejected [op=acquire_job]")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An ingestion job is already in progress for this repository.",
            )

        self.active_jobs.add(job_key)

    def release(self, user_id: str, repo_url: str):
        job_key = (user_id, repo_url)
        self.active_jobs.discard(job_key)


class IngestionRateLimiter:
    """
    Fixed-window rate limiter with memory bounds, lazy eviction, and Retry-After calculation.
    Tracks ingestion attempts per user_id and globally.
    """
    def __init__(self, limits: IngestionLimits):
        self.limits = limits
        self.user_records: Dict[str, List[float]] = {}
        self.global_records: List[float] = []

    def _cleanup_expired(self, now: float):
        # Clean global records
        cutoff_global = now - self.limits.GLOBAL_WINDOW_SECONDS
        self.global_records = [t for t in self.global_records if t > cutoff_global]

        # Clean user records
        cutoff_user = now - self.limits.USER_WINDOW_SECONDS
        expired_users = []
        for user_id, timestamps in self.user_records.items():
            valid_timestamps = [t for t in timestamps if t > cutoff_user]
            if valid_timestamps:
                self.user_records[user_id] = valid_timestamps
            else:
                expired_users.append(user_id)

        for user_id in expired_users:
            del self.user_records[user_id]

        # Evict oldest entries if map exceeds maximum bounds
        if len(self.user_records) > self.limits.MAX_RATE_LIMITER_MAP_SIZE:
            sorted_users = sorted(
                self.user_records.items(),
                key=lambda item: item[1][0] if item[1] else 0
            )
            excess = len(self.user_records) - self.limits.MAX_RATE_LIMITER_MAP_SIZE
            for u_id, _ in sorted_users[:excess]:
                del self.user_records[u_id]

    def check_and_record(self, user_id: str):
        now = time.time()

        # 1. Check Global Rate Limit
        cutoff_global = now - self.limits.GLOBAL_WINDOW_SECONDS
        active_global_requests = [t for t in self.global_records if t > cutoff_global]

        if len(active_global_requests) >= self.limits.MAX_GLOBAL_INGESTIONS_PER_WINDOW:
            oldest = active_global_requests[0]
            retry_after = int(max(1, cutoff_global - oldest + self.limits.GLOBAL_WINDOW_SECONDS))
            logger.warning("Global ingestion rate limit exceeded [op=rate_limit_check]")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Global ingestion rate limit reached. Please wait before retrying.",
                headers={"Retry-After": str(retry_after)},
            )

        # 2. Check Per-User Rate Limit
        cutoff_user = now - self.limits.USER_WINDOW_SECONDS
        user_requests = [t for t in self.user_records.get(user_id, []) if t > cutoff_user]

        if len(user_requests) >= self.limits.MAX_USER_INGESTIONS_PER_WINDOW:
            oldest = user_requests[0]
            retry_after = int(max(1, cutoff_user - oldest + self.limits.USER_WINDOW_SECONDS))
            logger.warning("User ingestion rate limit exceeded [op=rate_limit_check]")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Ingestion rate limit exceeded. Please wait before retrying.",
                headers={"Retry-After": str(retry_after)},
            )

        # Record attempt
        user_requests.append(now)
        self.user_records[user_id] = user_requests
        self.global_records.append(now)

        # Cleanup expired & evict excess keys
        self._cleanup_expired(now)


# Global singleton instances
DEFAULT_LIMITS = IngestionLimits()
concurrency_manager = IngestionConcurrencyManager(DEFAULT_LIMITS)
rate_limiter = IngestionRateLimiter(DEFAULT_LIMITS)


def validate_and_normalize_github_url(url: str, limits: IngestionLimits = DEFAULT_LIMITS) -> str:
    """
    Validates and normalizes GitHub repository URLs against strict security criteria.
    Accepts ONLY canonical GitHub HTTPS URLs:
    - https://github.com/owner/repository
    - https://github.com/owner/repository.git
    Returns normalized canonical URL: https://github.com/owner/repository
    """
    if not isinstance(url, str) or not url.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository URL must be a non-empty string.",
        )

    if len(url) > limits.MAX_REPO_URL_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository URL exceeds maximum allowed length.",
        )

    # Reject control characters or null bytes
    if re.search(r"[\x00-\x1f\x7f]", url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository URL contains invalid control characters.",
        )

    # Reject path traversal patterns
    if "%2e" in url.lower() or "%2f" in url.lower() or "%5c" in url.lower() or ".." in url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Repository URL contains invalid path elements.",
        )

    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed repository URL.",
        )

    # Must be HTTPS scheme
    if parsed.scheme != "https":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only HTTPS repository URLs are supported.",
        )

    # Hostname MUST be parsed exactly as 'github.com' (no subdomains, no substring matching)
    if not parsed.hostname or parsed.hostname.lower() != "github.com":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only repositories hosted on github.com are supported.",
        )

    # Must not contain embedded username or password
    if parsed.username or parsed.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Embedded credentials in repository URL are prohibited.",
        )

    # Must not contain custom ports (port 443 only / None)
    if parsed.port is not None and parsed.port != 443:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Custom ports in repository URL are prohibited.",
        )

    # Must not contain query parameters or fragments
    if parsed.query or parsed.fragment:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query parameters and fragments in repository URL are prohibited.",
        )

    # Validate path: must be exactly /owner/repository or /owner/repository.git
    clean_path = parsed.path.strip("/")
    parts = [p for p in clean_path.split("/") if p]

    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL must specify exactly a GitHub owner and repository name.",
        )

    owner, repo_part = parts[0], parts[1]

    # Validate owner & repo naming rules
    valid_name_regex = re.compile(r"^[a-zA-Z0-9_.-]+$")
    if not valid_name_regex.match(owner) or owner in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository owner name.",
        )

    if repo_part.endswith(".git"):
        repo_name = repo_part[:-4]
    else:
        repo_name = repo_part

    if not repo_name or not valid_name_regex.match(repo_name) or repo_name in (".", ".."):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GitHub repository name.",
        )

    canonical_url = f"https://github.com/{owner}/{repo_name}"
    return canonical_url


def is_ip_restricted(ip_str: str) -> bool:
    """
    Checks whether an IP address (IPv4 or IPv6) is restricted (private, loopback, link-local, multicast, etc.)
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # Invalid IP format treated as restricted

    # General checks
    if (
        ip.is_unspecified
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
    ):
        return True

    # IPv4 specific checks
    if ip.version == 4:
        # Carrier-grade NAT (100.64.0.0/10)
        cgnat_net = ipaddress.ip_network("100.64.0.0/10")
        if ip in cgnat_net:
            return True
        # Documentation ranges
        doc_nets = [
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
        ]
        if any(ip in net for net in doc_nets):
            return True

    # IPv6 specific checks
    if ip.version == 6:
        # IPv4-mapped IPv6 (::ffff:127.0.0.1)
        if ip.ipv4_mapped:
            return is_ip_restricted(str(ip.ipv4_mapped))
        # Unique local (fc00::/7)
        ula_net = ipaddress.ip_network("fc00::/7")
        if ip in ula_net:
            return True

    return False


def validate_dns_ip_safety(hostname: str = "github.com", timeout_sec: float = 5.0) -> bool:
    """
    Resolves hostname DNS and checks all returned IPv4 & IPv6 addresses against SSRF restricted IP ranges.
    Returns True if all resolved IPs are safe public addresses.
    Returns False if DNS lookup fails, times out, or resolves to any restricted IP.
    """
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout_sec)
        addr_info = socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)
        if not addr_info:
            logger.warning("DNS lookup returned empty result [op=validate_dns]")
            return False

        for family, sockaddr in [(info[0], info[4]) for info in addr_info]:
            ip_str = sockaddr[0]
            if is_ip_restricted(ip_str):
                logger.warning("DNS resolved to restricted IP address [op=validate_dns]")
                return False

        return True
    except Exception:
        logger.warning("DNS resolution failed or timed out [op=validate_dns]")
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def get_dir_size_bytes(dir_path: str) -> int:
    """
    Recursively measures total disk usage in bytes for a directory, inclusive of .git metadata and all files.
    """
    total = 0
    if not os.path.exists(dir_path):
        return 0
    for root, dirs, files in os.walk(dir_path):
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def terminate_process_tree(proc: subprocess.Popen):
    """
    Cross-platform process-tree termination adapter.
    On Windows: uses `taskkill /F /T /PID`.
    On Linux/Posix: kills process group via `os.killpg(SIGTERM)` followed by `SIGKILL`.
    Always reaps process to prevent orphan/zombie processes.
    """
    if proc.poll() is not None:
        return

    pid = proc.pid
    logger.info("Terminating process tree [op=terminate_proc_tree, pid=%d]", pid)

    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5)
        except Exception as e:
            logger.warning("Windows taskkill failed [op=terminate_proc_tree, exc_type=%s]", type(e).__name__)
            try:
                proc.kill()
            except Exception:
                pass
    else:
        try:
            getpgid_func = getattr(os, "getpgid", None)
            killpg_func = getattr(os, "killpg", None)
            if getpgid_func and killpg_func:
                pgid = getpgid_func(pid)
                killpg_func(pgid, signal.SIGTERM)
                time.sleep(0.5)
                if proc.poll() is None:
                    killpg_func(pgid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception as e:
            logger.warning("Linux killpg failed [op=terminate_proc_tree, exc_type=%s]", type(e).__name__)
            try:
                proc.kill()
            except Exception:
                pass

    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def run_safe_git_clone(canonical_url: str, temp_dir: str, timeout_sec: int) -> int:
    """
    Executes git clone in a separate process session/group with timeout and process-tree cleanup.
    Includes clone-time protections: --depth 1, --single-branch, --no-tags, --no-recurse-submodules.
    Returns returncode (0 on success). Raises subprocess.TimeoutExpired on timeout.
    """
    env = os.environ.copy()
    env["GIT_ALLOW_PROTOCOL"] = "https"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"

    cmd = [
        "git",
        "-c", "http.followRedirects=false",
        "-c", "core.symlinks=false",
        "clone",
        "--depth", "1",
        "--single-branch",
        "--no-tags",
        "--no-recurse-submodules",
        canonical_url,
        temp_dir,
    ]

    kwargs = {
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }

    if sys.platform != "win32":
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout_sec)
        return proc.returncode
    except subprocess.TimeoutExpired:
        logger.error("Git clone timed out [op=run_safe_git_clone, timeout=%d]", timeout_sec)
        terminate_process_tree(proc)
        raise
    except Exception as e:
        logger.error("Git clone process exception [op=run_safe_git_clone, exc_type=%s]", type(e).__name__)
        terminate_process_tree(proc)
        raise
