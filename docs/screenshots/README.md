# Cerebro Workspace Visual & Responsive QA Audit

This directory contains visual and responsive screenshots verifying the portfolio-ready design of Cerebro AI under local runtime rendering.

## 1. Visual Asset Mapping

| Filename | Viewport Width | Captures State | Description / Visual Checks Verified |
| :--- | :--- | :--- | :--- |
| `landing-1440.png` | 1440px | Desktop Landing | Glowing brand header, hero copy, and primary action targets. Proportional text flow and clear contrast. |
| `landing-1024.png` | 1024px | Tablet Landscape Landing | Safe margins, nav-bar wrapping protection, layout scaling preservation. |
| `landing-768.png` | 768px | Tablet Portrait Landing | Stacked hero copy, zero layout overlap, buttons adapt to mobile column grids. |
| `landing-375.png` | 375px | Mobile Standard Landing | Clean font-scaling, min-44px touch targets, hidden navigation drawer controls. |
| `landing-320.png` | 320px | Mobile Small Landing | Proportional grid sizing, wraps headers safely, zero horizontal page overflow. |
| `grounded-workspace-1440.png` | 1440px | Grounded Search Workspace | Active multi-repo sidebar list, Search inputs, and a fully rendered code snippet explanation. |
| `citation-expanded-1024.png` | 1024px | Expanded Citation Panel | Timing metadata block, match score confidence badges, and expanded file-slicing code viewer. |
| `no-evidence-768.png` | 768px | No-Evidence Search Result | Search panel showing graceful zero-result summary, alternative query suggestions, and limitations. |
| `error-state-768.png` | 768px | API Rate Limit (HTTP 429) | Distinct visual alert banner (bold type, warning icon, safe descriptive copy) rendered inline. |
| `mobile-drawer-375.png` | 375px | Mobile Sidebar Drawer Open | Mobile sliding sidebar drawer containing active repository tokens, user profile, and sign-out controls. |
| `graph-table-fallback-1024.png` | 1024px | Accessible Table Fallback | Knowledge Map tab showing screen-reader accessible structured table representing file metrics. |

---

## 2. QA Checklist Summary

- **Horizontal Overflow**: No horizontal scrollbars or overflow were detected across any tested viewports. The layout scales proportionally.
- **Overlapping Text**: Text containers utilize proper word-wrapping and do not overlap into neighboring elements on smaller devices (320px & 375px).
- **Button Reachability**: CTA buttons and navigation elements remain large enough (min 44px hit area) and easily reachable on mobile viewports.
- **Font Size**: Font sizes scale appropriately; no text is rendered too small to read on mobile.
- **Contrast**: UI elements and text maintain sufficient contrast against their backgrounds for readability.
- **WAI-ARIA Accessibility**: Verified keyboard-only focus rings, skip-to-content links, and semantic tags (`role="tablist"` / `role="tab"`).
- **Data Compliance**: Clean fixture and dummy authentication values only. No production tokens, credentials, or private query logs are included.
