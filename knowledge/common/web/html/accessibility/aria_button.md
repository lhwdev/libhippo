---
title: "Button Accessibility with ARIA"
namespace: "common"
level: "leaf"
coarseness: 3
version: "WAI-ARIA 1.2"
status: "active"
last_updated: "2026-09-28"
related:
  - "common/web/html/syntax/semantic_tags.md"
tags: ["html", "a11y", "aria", "button"]
access_count: 0
last_accessed: "2026-09-28"
nature: "critical_rule"
importance: 0.85
---

## Summary (Coarse View)
Use native `<button>` whenever possible. Only use `role="button"` on `<div>` with `tabindex="0"` and keyboard listeners (Enter/Space).

## Detailed Rules & Edge Cases (Fine View)
- Ensure preventDefault() on Space key to avoid page scroll.
- Add aria-pressed for toggle buttons.
