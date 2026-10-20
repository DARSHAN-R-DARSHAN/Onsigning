# Onsigning Design System

**Status:** Initial product design baseline  
**Applies to:** Marketing website, authentication, application shell, dashboard, contract workflows, tables, dialogs, reports, and responsive UI  
**Product principle:** Calm, evidence-first, enterprise-ready contract intelligence.

## 1. Design principles

1. **Trust through clarity:** communicate what the system knows, where the evidence came from, and what needs human review.
2. **Action before decoration:** prioritize deadlines, unresolved findings, and next steps over decorative metrics.
3. **Evidence beside conclusions:** users should be able to inspect the original contract passage behind a finding or answer.
4. **Consistent patterns:** all four MVP features share navigation, typography, spacing, component behavior, and status semantics.
5. **Accessible by default:** design for keyboard, screen readers, responsive layouts, and sufficient contrast.
6. **Honest product communication:** do not imply legal verification, security guarantees, integrations, or compliance certifications that have not been implemented and validated.

## 2. Brand color direction

The overall visual direction is approximately **80% white and neutral surfaces, 20% blue accents**. This is a composition guideline rather than a strict per-component calculation. Blue should focus attention, not fill every element.

### Core tokens

| Token | Hex | Usage |
|---|---|---|
| `--color-white` | `#FFFFFF` | Main cards, forms, panels, primary surfaces |
| `--color-canvas` | `#F5F9FF` | Subtle page background or grouped sections |
| `--color-blue-50` | `#F0F7FF` | Soft highlighted surfaces |
| `--color-blue-100` | `#EAF2FF` | Selected navigation and subtle selection |
| `--color-blue-200` | `#DCEBFF` | Light emphasis and selected borders |
| `--color-blue-300` | `#93C5FD` | Non-text decorative accents |
| `--color-blue-500` | `#3B82F6` | Secondary accents; check contrast before text use |
| `--color-primary` | `#2563EB` | Primary buttons, links, active controls |
| `--color-primary-hover` | `#1D4ED8` | Hover/pressed emphasis |
| `--color-navy` | `#172B4D` | Headings and primary text |
| `--color-text-secondary` | `#64748B` | Secondary text; verify contrast for small text |
| `--color-border` | `#DCE6F1` | Dividers, outlines, table borders |
| `--color-focus` | `#2563EB` | Visible keyboard focus indicator |

### Semantic status & risk severity tokens (AUD-006, AUD-013)

| Status / Severity | Text / Icon Hex | Surface Hex | Border Hex | Meaning & Contrast |
|---|---|---|---|---|
| `CRITICAL` / overdue | `#B42318` | `#FEF3F2` | `#FECDCA` | Urgent attention required (7.2:1 contrast) |
| `HIGH` severity | `#C2410C` | `#FFF7ED` | `#FFEDD5` | High contract risk breach (5.1:1 contrast) |
| `MEDIUM` / due soon | `#92400E` | `#FEFCE8` | `#FEF08A` | Review needed / moderate risk (6.5:1 contrast) |
| `LOW` / info | `#1D4ED8` | `#EFF6FF` | `#DBEAFE` | Minor policy deviation (6.2:1 contrast) |
| `COMPLIANT` / confirmed | `#166534` | `#F0FDF4` | `#DCFCE7` | Meets playbook requirements (6.8:1 contrast) |
| `UNDECIDED_NEEDS_REVIEW` | `#475569` | `#F8FAFC` | `#E2E8F0` | Missing coverage or ambiguous (5.3:1 contrast) |
| Muted / draft | `#64748B` | `#F1F5F9` | `#CBD5E1` | Inactive, draft, or unindexed |

> [!WARNING]
> `--color-blue-500` (`#3B82F6`) has a contrast ratio of only 3.2:1 against white and fails WCAG 2.2 AA for normal text. Use `#3B82F6` solely for non-text borders and decorative accents. For text on light backgrounds, use `--color-primary` (`#2563EB`, 4.6:1) or `--color-navy` (`#172B4D`, 12.3:1).

## 3. Tailwind CSS and shadcn/ui token mapping (AUD-013)

To ensure seamless integration with `shadcn/ui` and `Tailwind CSS`, map the design system tokens directly into CSS variables used by Tailwind:

```css
:root {
  color-scheme: light;

  /* Onsigning core surfaces & text */
  --color-white: #ffffff;
  --color-canvas: #f5f9ff;
  --color-blue-50: #f0f7ff;
  --color-blue-100: #eaf2ff;
  --color-blue-200: #dcebff;
  --color-blue-300: #93c5fd;
  --color-blue-500: #3b82f6;
  --color-primary: #2563eb;
  --color-primary-hover: #1d4ed8;
  --color-navy: #172b4d;
  --color-text-secondary: #64748b;
  --color-border: #dce6f1;
  --color-focus: #2563eb;

  /* shadcn/ui standard theme variables */
  --background: 216 100% 98%;          /* #f5f9ff (--color-canvas) */
  --foreground: 217 54% 19%;           /* #172b4d (--color-navy) */
  --card: 0 0% 100%;                   /* #ffffff (--color-white) */
  --card-foreground: 217 54% 19%;      /* #172b4d (--color-navy) */
  --popover: 0 0% 100%;                /* #ffffff (--color-white) */
  --popover-foreground: 217 54% 19%;
  --primary: 221 83% 53%;              /* #2563eb (--color-primary) */
  --primary-foreground: 0 0% 100%;
  --secondary: 213 100% 97%;           /* #f0f7ff (--color-blue-50) */
  --secondary-foreground: 221 83% 53%;
  --muted: 214 32% 91%;                /* #dce6f1 (--color-border) */
  --muted-foreground: 215 16% 47%;     /* #64748b (--color-text-secondary) */
  --accent: 214 100% 96%;              /* #eaf2ff (--color-blue-100) */
  --accent-foreground: 217 54% 19%;
  --destructive: 4 86% 41%;            /* #b42318 (--color-danger) */
  --destructive-foreground: 0 0% 100%;
  --border: 214 32% 91%;               /* #dce6f1 (--color-border) */
  --input: 214 32% 91%;                /* #dce6f1 (--color-border) */
  --ring: 221 83% 53%;                 /* #2563eb (--color-focus) */

  --radius-sm: 0.375rem;
  --radius-md: 0.5rem;
  --radius-lg: 0.75rem;
  --radius-xl: 1rem;

  --shadow-card: 0 1px 2px rgb(23 43 77 / 5%);
  --shadow-popover: 0 8px 24px rgb(23 43 77 / 12%);
}
```

## 4. Typography

- Prefer the existing product font if already established. Otherwise use a clean system sans-serif stack; do not add a font dependency solely for decoration.
- Use sentence case for navigation, buttons, headings, and labels.
- Use clear hierarchy rather than many font styles.
- Suggested scale:
  - Page title: 28–32 px desktop; 24–28 px mobile.
  - Section heading: 18–22 px.
  - Card heading: 15–18 px.
  - Body: 14–16 px.
  - Dense table text: 13–14 px where legible.
  - Helper text: 12–14 px; avoid low-contrast tiny text.
- Body line-height should generally be around 1.45–1.65.
- Contract excerpts may use a document-appropriate serif font only when it improves source-document readability; application chrome stays sans-serif.

## 5. Layout, spacing, and surfaces

- Use a consistent spacing scale based on 4 px: 4, 8, 12, 16, 24, 32, 40, 48, 64.
- Use a centered, responsive max-width for marketing content. Keep reading line lengths comfortable.
- Use a persistent left sidebar on wide application layouts when appropriate; collapse it to a drawer or compact navigation on smaller screens.
- Keep page headers consistent: title and context on the left, primary action on the right where space allows.
- Use white cards on white or very light blue backgrounds, separated by subtle borders and spacing. Avoid stacking unnecessary nested cards.
- Use shadows sparingly; prefer spacing and borders for structure.
- Use sticky headers only when they improve long-table or document navigation and do not obscure content.
- Avoid horizontal page overflow. Dense tables may use a contained horizontal scroll region with a clear responsive alternative.

## 6. Component rules

### Buttons
- Primary: solid `--color-primary` with white text.
- Secondary: white surface, border, navy text.
- Tertiary: low-emphasis text button for less frequent actions.
- Destructive: semantic danger treatment and a clear action label.
- Include hover, focus-visible, active, disabled, and loading states.
- Button text must describe the action (for example, “Upload agreement”, “Confirm finding”, “Save changes”), not generic “Submit” where a clearer label fits.
- Do not place multiple competing primary buttons in one visual region.

### Links and navigation
- Links use primary blue and remain distinguishable from surrounding text.
- Active navigation may use pale blue background, primary blue icon/text, and a clear active indicator.
- Include accessible names and visible keyboard focus.
- Do not use clickable-looking styling on non-interactive text.

### Forms
- Every input has a persistent visible label; placeholders are examples, not labels.
- Explain validation errors next to the relevant field and preserve entered values when possible.
- Mark required fields accessibly.
- Avoid clearing a form after an error.
- Confirm destructive or irreversible actions with clear context.

### Cards and metrics
- Show a metric only when its definition and source are clear.
- Every dashboard metric should help a user decide what to do next.
- Avoid fabricated or hardcoded demo metrics in production UI.
- Use an empty state with a useful next action when there is no data.

### Tables
- Use clear column labels, readable row spacing, sorting/filtering only when implemented, and visible empty/error states.
- Keep key identifiers and due dates easy to scan.
- On narrow screens, prioritize key fields and provide a usable detail view or contained horizontal scrolling.
- Do not display action icons without accessible labels or tooltips.

### Status and severity
- Always show text such as “Overdue”, “Due soon”, “Needs verification”, or “Confirmed by reviewer”.
- Keep status meanings consistent throughout the application.
- Do not treat “AI extracted” as equivalent to “human verified”.
- Severity labels must follow product definitions, not arbitrary model confidence.

### Dialogs, toasts, and notifications
- Use dialogs for focused decisions and confirmation, not for every small interaction.
- Provide clear titles, actions, keyboard behavior, and focus management.
- Toasts should be brief, accessible, and not the only way to communicate a persistent error.
- Notification preferences should avoid unnecessary noise and respect configured recipients/time zones.

## 7. Marketing website

### Primary goal
Help the right prospective customer understand the post-signature contract problem, see how Onsigning solves it, and request a conversation or demo.

### Recommended content hierarchy
1. Clear outcome-focused headline and concise supporting copy.
2. One primary CTA, such as “Request a demo”, plus a lower-emphasis secondary action if useful.
3. A genuine product screenshot or accurate prototype showing contract evidence, obligations, or deadlines.
4. The post-signature problem and why existing storage alone does not solve it.
5. How the workflow works: upload agreement → inspect evidence-backed findings → confirm obligations → track follow-through.
6. The four MVP capabilities, described without overpromising.
7. Security/data-handling information only to the extent it is accurate and implemented.
8. A final CTA and clear contact path.

Avoid generic AI claims, fake customer logos, fabricated testimonials, unsupported compliance badges, excessive animations, and decorative gradients that compete with the product.

## 8. Application shell and dashboard

### Suggested navigation (AUD-016)
- **Overview** (Executive summary, urgent deadlines, pending reviews)
- **Contracts** (Agreements list, upload modal, playbook audit reports, contract families)
- **Obligations & Deadlines** (Obligations table, calendar views, assignments)
- **Ask Contracts** (Cross-contract repository Q&A workspace)
- **Activity Log** (System mutation and user access trail; distinguishes security logs from contract playbook audits)
- **Settings** (Workspace members, roles, risk playbooks, integrations)

Adjust names only when the existing PRD or implemented information architecture requires it.

### Dashboard priority order
1. Items requiring attention: overdue obligations, near deadlines, unresolved findings, processing failures.
2. Agreements needing review or confirmation.
3. Recent changes and audit activity.
4. Secondary metrics only when they support real decisions.

Do not let large metric cards push urgent work below the fold. Show clear date/time-zone context for deadlines where relevant.

## 9. UX requirements for the four MVP features

### Feature 1 — Single-Agreement Playbook Audit (AUD-018)
#### Side-by-side agreement review workspace
- **Desktop (>= 1024px):** 55% Document Viewer on the left (PDF.js canvas with page navigation, zoom, in-page search, and verified citation bounding highlights); 45% Findings Rail on the right (playbook metadata, severity rollup, risk alerts list, coverage warning banners, and review controls).
- **Tablet (768px – 1023px):** Findings rail collapses into a right-hand drawer or segmented control ("Document" | "Playbook Report").
- **Mobile (< 768px):** Single-column stacked layout with a sticky bottom summary bar indicating unresolved alerts and citation links that smoothly transition between findings and source pages.
- **Keyboard accessibility:** 
  - `j` / `k` (or `ArrowDown` / `ArrowUp`): navigate sequentially through risk alert cards.
  - `Enter` on an alert card: scrolls the PDF viewer directly to the verified page and highlights the quote.
  - `c`: opens confirmation dialog; `o`: opens override note modal; `d`: opens dismissal dialog.
  - Standard `Tab` / `Shift+Tab` maintains logical focus order across buttons and panels.

#### Interaction workflows
- Distinguish extracted source text, rule evaluator interpretations, and human decisions.
- Support human review actions corresponding strictly to `SCHEMA.md` review statuses:
  - **Confirm Finding** → transitions alert to `HUMAN_CONFIRMED`.
  - **Override / Edit Finding** → transitions alert to `HUMAN_OVERRIDDEN` with mandatory explanation note.
  - **Dismiss False Positive** → transitions alert to `DISMISSED_FALSE_POSITIVE` with mandatory justification note.
  - **Reassign for Second Review** → marks `NEEDS_HUMAN_REVIEW` with an assigned user.
- Explicitly surface coverage warnings: If unreadable pages or unattached exhibits were detected during parsing, display a persistent amber warning header above findings.

### Feature 2 — Contract Families and Amendment Management
- Present the master agreement and related documents in a clear family/group view.
- Show amendment chronology and the status of each document.
- Do not silently infer that an amendment overrides a clause.
- Require human confirmation for relationships or changes that the product specification marks as uncertain or consequential.
- Make the source for each displayed change inspectable.

### Feature 3 — Cross-Contract Q&A
- Keep the question composer simple and prominent.
- Ground answers in the authorized agreements available to the user.
- Attach citations to specific agreements and passages when supported.
- Make insufficient evidence, conflicting clauses, and unanswered questions explicit.
- Do not present unsupported conclusions as facts.

### Feature 4 — Obligation and Deadline Automation
- Use a scannable table or list with obligation, agreement, owner, due date, status, and source clause where available.
- Support useful filters and sorting only when implemented.
- Make reminder configuration, recipients, and time-zone handling explicit where supported.
- Show overdue and upcoming states with text plus color/icon.
- Preserve human confirmation and auditability for extracted or changed obligations.

## 10. Canonical state mappings and interaction states (AUD-006)

UI badges and workflow cards must map 1:1 to canonical database enums from `SCHEMA.md`. Never introduce unmapped ad-hoc states in application components.

### 10.1 Document Processing States (`doc_processing_status`)
| Backend Enum Value | Display Label | Badge Styling | Interactive Behavior |
|---|---|---|---|
| `UPLOADED` | "Uploaded" | Canvas neutral | Enqueued for parsing |
| `PARSING_LAYOUT` | "Parsing document..." | Blue-50 text-primary | Spinner active |
| `EXTRACTING_FACTS` | "Extracting terms..." | Blue-50 text-primary | Spinner active |
| `EVALUATING_PLAYBOOK` | "Evaluating playbook..." | Blue-50 text-primary | Spinner active |
| `READY` | "Audit Complete" | Success green | Report ready for review |
| `READY_WITH_WARNINGS` | "Complete (Warnings)" | Warning amber | Report ready; banner highlights gaps |
| `FAILED` | "Processing Failed" | Danger red | Displays `error_message` & "Retry" button |

### 10.2 Finding Review Statuses (`review_status`)
| Backend Enum Value | Display Label | Badge Styling | Description |
|---|---|---|---|
| `AI_EVALUATED` | "AI Evaluated" | Blue-100 text-navy | Algorithmic finding pending initial user review |
| `NEEDS_HUMAN_REVIEW` | "Needs Review" | Warning amber | Flagged due to approximate citation or ambiguity |
| `HUMAN_CONFIRMED` | "Confirmed" | Success green | Confirmed by authorized reviewer |
| `HUMAN_OVERRIDDEN` | "Overridden" | Info blue | Human modified finding severity or facts |
| `DISMISSED_FALSE_POSITIVE` | "Dismissed" | Muted slate | Dismissed by reviewer with audit note |

### 10.3 Citation Verification Badges (`citation_verification_status`)
| Backend Enum Value | Display Label | Icon & Color | Verification Meaning |
|---|---|---|---|
| `EXACT_MATCH` | "Exact Quote" | Checkmark `#166534` | Contiguous exact match on source page (`verified=true`) |
| `NORMALIZED_MATCH` | "Verified Match" | Checkmark `#166534` | Matches under strict normalization (`verified=true`) |
| `APPROXIMATE_NEEDS_REVIEW` | "Approximate" | Alert triangle `#92400E` | Partial match; unverified, manual review required |
| `UNVERIFIED_MISMATCH` | "Unverified" | Exclamation circle `#B42318` | Source text mismatch; unverified |
| `NOT_APPLICABLE` | "Omission / N/A" | Dash `#64748B` | Non-explicit clause; no quote expected |

### 10.4 Feature 1 Core Component States
- **Upload Dropzone State:** Border dashed `--color-border`; drag-active highlights `--color-blue-100` and `--color-primary`. Validates `.pdf` and `.docx` magic bytes on drop. Rejects legacy `.doc` immediately with user-facing message: *"Legacy .doc files are not supported. Please upload a .docx or .pdf file."*
- **Empty State:** Clean white card on canvas background with clear CTA: *"Upload a signed agreement to run your first Risk Playbook audit."*
- **Failure / Error State:** Non-intrusive alert card displaying sanitized `error_message`, failure timestamp, and a primary "Retry Processing" button triggering an idempotent job dispatch. Technical stack traces are never exposed in user-facing UI.

## 11. Accessibility and responsive behavior

- Target WCAG 2.2 AA where practical.
- Ensure keyboard access, visible focus, semantic headings, accessible names, and logical focus order.
- Check text and control contrast, including hover, disabled, and selected states.
- Do not use color as the only way to convey meaning.
- Support browser zoom and narrow viewports without clipping important actions.
- Provide alternatives to drag-only interactions.
- Respect reduced-motion preferences; avoid motion that is not functional.
- Test at a minimum of a narrow mobile viewport and a typical desktop viewport.

## 12. Trust, privacy, and security presentation

- Show only agreements the current user is authorized to access.
- Do not leak document text through client logs, error messages, telemetry, or URL parameters.
- Treat contract content as untrusted input.
- Make AI-data practices and retention statements factual and consistent with implementation.
- Do not claim encryption, certifications, regulatory compliance, or guaranteed legal accuracy unless verified and approved.
- Explain consequential actions before execution and retain audit history where required by the product specification.

## 13. Implementation and review checklist

Before marking UI work complete, verify:

- [ ] The existing architecture and source-of-truth docs were read.
- [ ] Shared design tokens and components were reused.
- [ ] No unexplained hardcoded colors or competing visual patterns were added.
- [ ] Primary, secondary, disabled, loading, error, empty, and success states were considered.
- [ ] Buttons and links perform the actions they advertise.
- [ ] Keyboard navigation and visible focus work.
- [ ] Contrast and accessible labels were checked.
- [ ] Layout was inspected on mobile and desktop.
- [ ] Contract findings/answers show grounded evidence where available.
- [ ] Human confirmation and uncertainty are represented honestly.
- [ ] Tests, lint, type checks, and build checks applicable to the change were run.
- [ ] Any incomplete checks or known limitations are reported honestly.
