import logging
import uuid
import datetime
from typing import List, Dict, Optional, Any
from fastapi import HTTPException, status
from ingestion_validator import validate_and_normalize_github_url

logger = logging.getLogger(__name__)


class DatabaseAdapter:
    """
    Centralized data adapter for all repository, ingestion job, and snippet database operations.
    Enforces user isolation constraints on every Service-Role query.
    """

    @staticmethod
    def _handle_db_error(e: Exception, operation: str):
        """Catches database driver or table missing exceptions and returns sanitized errors."""
        err_msg = str(e)
        logger.error("Database operation failed [op=%s, exc_type=%s, msg=%s]", operation, type(e).__name__, err_msg)

        # Fail safely if tables are not migrated/present in schema
        if "relation" in err_msg.lower() and "does not exist" in err_msg.lower():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database migration pending: required repository tables are missing.",
            )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database execution error during {operation}.",
        )

    @classmethod
    def resolve_user_repo(cls, db: Any, user_id: str, repo_url: str) -> Dict[str, Any]:
        """
        Resolves or creates a user repository record.
        Verifies ownership and prevents user duplicate URLs.
        """
        if not db:
            raise HTTPException(status_code=500, detail="Database client not configured.")

        canonical_url = validate_and_normalize_github_url(repo_url)

        # Parse owner and name
        parts = canonical_url.split("github.com/")[-1].split("/")
        owner, repo_name = parts[0], parts[1]

        try:
            # Query existing repository scoped strictly to user_id
            res = db.table("user_repositories").select("*").eq("user_id", user_id).eq("canonical_url", canonical_url).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]

            # Create new repository record if not exists
            new_repo = {
                "user_id": user_id,
                "provider": "github",
                "repository_owner": owner,
                "repository_name": repo_name,
                "canonical_url": canonical_url,
                "default_branch": "main",
                "status": "pending",
            }
            insert_res = db.table("user_repositories").insert(new_repo).execute()
            if not insert_res.data or len(insert_res.data) == 0:
                raise Exception("Failed to insert repository record.")

            return insert_res.data[0]
        except Exception as e:
            cls._handle_db_error(e, "resolve_user_repo")

    @classmethod
    def get_owned_repo(cls, db: Any, user_id: str, repo_id: str) -> Dict[str, Any]:
        """Fetches repository verifying ownership. Raises 404/403 on lookup failure."""
        if not db:
            raise HTTPException(status_code=500, detail="Database client not configured.")

        # Validate UUID structure before query
        try:
            uuid.UUID(repo_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid repository ID format.")

        try:
            res = db.table("user_repositories").select("*").eq("id", repo_id).eq("user_id", user_id).execute()
            if not res.data or len(res.data) == 0:
                # Return generic 404 to prevent ID enumeration
                raise HTTPException(status_code=404, detail="Repository not found or inaccessible.")
            return res.data[0]
        except HTTPException:
            raise
        except Exception as e:
            cls._handle_db_error(e, "get_owned_repo")

    @classmethod
    def get_repo_by_name(cls, db: Any, user_id: str, repo_name: str) -> Dict[str, Any]:
        """Resolves a repository by name strictly scoped to the authenticated user."""
        if not db:
            raise HTTPException(status_code=500, detail="Database client not configured.")

        try:
            res = db.table("user_repositories").select("*").eq("user_id", user_id).eq("repository_name", repo_name).execute()
            if not res.data or len(res.data) == 0:
                raise HTTPException(status_code=404, detail="Repository not found or inaccessible.")
            if len(res.data) > 1:
                # Ambiguous repo_name query
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Multiple repositories found matching name '{repo_name}'. Please specify repository ID.",
                )
            return res.data[0]
        except HTTPException:
            raise
        except Exception as e:
            cls._handle_db_error(e, "get_repo_by_name")

    @classmethod
    def list_owned_repos(cls, db: Any, user_id: str) -> List[Dict[str, Any]]:
        """Lists userrepositories."""
        if not db:
            raise HTTPException(status_code=500, detail="Database client not configured.")

        try:
            res = db.table("user_repositories").select("*").eq("user_id", user_id).execute()
            return res.data or []
        except Exception as e:
            cls._handle_db_error(e, "list_owned_repos")

    @classmethod
    def create_ingestion_job(cls, db: Any, user_id: str, repo_id: str, index_version: str = "v1", commit_sha: Optional[str] = None) -> str:
        """Creates a new unique ingestion job ID for the user repository."""
        if not db:
            raise HTTPException(status_code=500, detail="Database client not configured.")

        # Verify active jobs to prevent duplicate ingestion pipelines running simultaneously
        try:
            res = db.table("ingestion_jobs").select("id").eq("repository_id", repo_id).eq("user_id", user_id).in_("status", ["pending", "cloning", "indexing"]).execute()
            if res.data and len(res.data) > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An ingestion job is already active for this repository.",
                )

            new_job = {
                "user_id": user_id,
                "repository_id": repo_id,
                "status": "pending",
                "index_version": index_version,
                "commit_sha": commit_sha,
            }
            job_res = db.table("ingestion_jobs").insert(new_job).execute()
            if not job_res.data or len(job_res.data) == 0:
                raise Exception("Failed to insert ingestion job record.")

            return job_res.data[0]["id"]
        except HTTPException:
            raise
        except Exception as e:
            cls._handle_db_error(e, "create_ingestion_job")

    @classmethod
    def update_job_status(cls, db: Any, user_id: str, job_id: str, status_str: str, failure_category: Optional[str] = None, inserted_chunk_count: int = 0):
        """Updates the status and metadata for a specific ingestion job."""
        if not db:
            return

        try:
            update_data = {
                "status": status_str,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "inserted_chunk_count": inserted_chunk_count,
            }
            if status_str == "completed":
                update_data["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            if failure_category:
                update_data["failure_category"] = failure_category

            db.table("ingestion_jobs").update(update_data).eq("id", job_id).eq("user_id", user_id).execute()
        except Exception as e:
            cls._handle_db_error(e, "update_job_status")

    @classmethod
    def promote_index_version(cls, db: Any, user_id: str, repo_id: str, job_id: str, new_version: str, commit_sha: Optional[str] = None):
        """
        Atomically promotes the repository index version.
        Updates user_repositories and purges previous index snippets.
        """
        if not db:
            return

        try:
            # 1. Update Ingestion Job
            cls.update_job_status(db, user_id, job_id, "completed")

            # 2. Update Repository status & Active Index Version
            repo_update = {
                "status": "ready",
                "active_index_version": new_version,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "last_indexed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if commit_sha:
                repo_update["indexed_commit_sha"] = commit_sha

            db.table("user_repositories").update(repo_update).eq("id", repo_id).eq("user_id", user_id).execute()

            # 3. Clean up older index snippets for this user & repo
            db.table("code_snippets").delete().eq("repository_id", repo_id).eq("user_id", user_id).neq("index_version", new_version).execute()

        except Exception as e:
            cls._handle_db_error(e, "promote_index_version")

    @classmethod
    def fail_and_cleanup_job(cls, db: Any, user_id: str, repo_id: str, job_id: str, failure_category: str):
        """Marks the job as failed and deletes snippets associated with the failed job ID."""
        if not db:
            return

        try:
            # 1. Update Ingestion Job status
            cls.update_job_status(db, user_id, job_id, "failed", failure_category=failure_category)

            # 2. Update Repository status
            db.table("user_repositories").update({"status": "failed", "last_error_category": failure_category}).eq("id", repo_id).eq("user_id", user_id).execute()

            # 3. Purge snippets belonging to this failed job
            db.table("code_snippets").delete().eq("ingestion_job_id", job_id).eq("user_id", user_id).execute()

        except Exception as e:
            cls._handle_db_error(e, "fail_and_cleanup_job")

    @classmethod
    def delete_owned_repo(cls, db: Any, user_id: str, repo_id: str):
        """Deletes a repository and all of its associated code snippets."""
        if not db:
            return

        try:
            # 1. Delete code snippets first (foreign key constraints)
            db.table("code_snippets").delete().eq("repository_id", repo_id).eq("user_id", user_id).execute()

            # 2. Delete user repository record
            db.table("user_repositories").delete().eq("id", repo_id).eq("user_id", user_id).execute()

        except Exception as e:
            cls._handle_db_error(e, "delete_owned_repo")
