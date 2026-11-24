# Job Dashboard

Local dashboard that finds SDE-1, new-grad, and 0–2-year roles in India. It tailors a one-page résumé for each job and applies when you click **Apply**.

## Start
```powershell
cd job-dashboard
.\start.ps1          # opens http://127.0.0.1:8765, logs to data\server.log
```

## How it works
- **Discovery** runs every 60 s (`settings.json` → `poll_seconds`) and whenever you press **Refresh**. It checks Google, Microsoft, Amazon, and the Greenhouse, Lever, and Ashby boards listed in `settings.json`.
- New roles flash purple, beep, and show a desktop notification. Nothing is submitted automatically.
- **Apply** (single or bulk) tailors a résumé into Downloads as `Ashish_Simon_Rattan_<Company>_resume.pdf` and fills the form in a visible Edge window. It then submits, and marks the job green only when the portal confirms.

## Colour legend
| Colour | Meaning |
|---|---|
| Purple | New since you last looked |
| Grey | Not applied |
| Blue | Tailoring / applying |
| Amber | **Needs you**: sign-in portal (Google, Microsoft, Amazon, Workday), unanswered required question, or CTC/notice-period field. The tab stays open with the résumé ready. |
| Red | Blocked (CAPTCHA, closed posting) or error |
| Green | Submitted (confirmed) |
| Slate | Skipped |

## Customising
- `profile.json`: form answers. Fill `notice_period`, `current_ctc`, `expected_ctc`, and `github` to cut down on amber jobs.
- `settings.json`: company boards, poll interval, and `auto_submit` (set it to `false` to pre-fill only).
- `resume/base_resume.tex`: base résumé. Tailoring changes only the profile summary and skill order.

CAPTCHAs and sign-ins are never bypassed. Every application is also recorded in `Job Applications.xlsx`.
