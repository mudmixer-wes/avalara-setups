# Avalara Migration Notes

## Purpose

This workspace is for the Avalara company-code migration project for MudMixer.

Current phase:
- Configure returns under the new Avalara company code.
- Do not file tax returns.
- Be cautious with any destructive action.

## Source Of Truth

Use `return-action-review.csv` as the working progress document.

Rules for that sheet:
- `ai_completed` is the global completion field, regardless of whether work was done by AI or manually by the user/team.
- Before editing the sheet, create a timestamped backup in `backups/`.
- Keep edits narrow. Do not reorder rows unless explicitly asked.
- Record what was done in `reviewer_notes`.
- Existing rows describe the legacy return; migration work is usually documented by updating that row's notes rather than adding new rows.

## Safety Rules

- Be extra sensitive to destructive actions such as expiring or deleting returns.
- If the correct end date, filing period, or setup choice is unclear, pause and ask the user.
- When setting up a return through the UI, finish the intended flow and click `Done with <state>` when that step is available.
- On Avalara return setup screens, `Company information` fields such as `Legal entity name` may be prepopulated.
- Treat clearing those fields as mandatory, not optional.
- Explicitly blank the textbox first, verify it is empty, and only then type the intended value.
- Do not rely on overwrite behavior. If the field is not visibly empty before typing, stop and clear it again.
- Failure mode already observed: Avalara can persist concatenated entity names such as `MudMixer LLC (NEW)MudMixer, LLC` if text is appended instead of replacing the prefilled value.
- If normal browser fill still concatenates text instead of replacing it, use a hard clear or direct input-value setter before saving.
- Do not invent missing registration data, IDs, or state-specific fields.
- Do not duplicate passwords or other secrets into this file. Use the CSV/JSON exports or Avalara UI/API when needed.

UI operating rules:
- Prefer the interactive Avalara UI path over direct route hacks when a normal path exists.
- If a Returns page or deep link gets into a broken or stale state:
  - leave any sensitive in-progress tab intact if needed
  - open a fresh tab
  - navigate to `Home`
  - confirm the company switcher is on the intended company
  - re-enter Returns through the UI
- When a state portal requires inspection or setup, keep following the portal flow until the next consequential or potentially destructive decision point.

Parent-company tax-calculation rule:
- After adding a return in the new returns company, check the state tile for tax-calculation warnings.
- If a tile says `You added this region but need to set up tax calculation via the parent company`, switch to parent company `DEFAULT` before doing anything else in that state.
- In the parent company, use `Settings > Where you report tax` at `https://app.avalara.com/nexus`.
- Add the state there through the guided UI, then switch back to `MUDMIXERLLCNEW`.
- Do not assume the child company can resolve tax-calculation setup by itself; the new returns company has `parentCompanyId = 6359760` and `hasProfile = false`.

Alaska-specific tax-calculation note:
- Alaska tax calculation was fixed in the parent company, not the child company.
- In the parent company's Alaska flow, select `Sales and use`.
- Use the guided option `I'm registered with the Alaska Remote Sellers Sales Tax Commission`.
- That option auto-enables the ARSSTC member local taxes instead of manually selecting Alaska locals one by one.

## Account And Company Facts

- Avalara account ID: `2006428542`
- Legacy company code: `DEFAULT`
- Legacy company ID: `6359760`
- New company code: `MUDMIXERLLCNEW`
- New company ID: `6550943`

Legal entities:
- Wrong legacy entity on many old returns: `OJMD Partnership`
- Wrong EIN: `45-3991770`
- Correct target entity: `MudMixer, LLC`
- Correct EIN: `47-3097812`
- Legacy company name that may still appear in records: `Red Dog Mobile Shelters, LLC`

Cutover:
- NetSuite and Shopify should accrue to the new company code starting `2026-03-01`.

## Business Footprint

Core business facts:
- MudMixer sells physical goods.
- MudMixer uses 3PL warehouse partners that hold MudMixer-owned inventory in:
  - Washington
  - California
  - Kansas

Office / headquarters facts:
- Executive offices are in Lubbock, Texas.
- Primary HQ is in Franklin, Tennessee.

Operational interpretation for return setup:
- Do not treat 3PL warehouses as company offices or employee locations unless the user says otherwise.
- Do treat 3PL inventory locations as relevant when a state tax question is asking about inventory, warehousing, physical presence, or nexus-related facts.
- If a form asks about company offices, employees, or business addresses, use the actual company office/HQ facts above, not the 3PL states.
- If a form asks about registered in-state locations, outlets, or separately registered business locations, do not assume a 3PL warehouse counts; pause if the wording is legally or operationally ambiguous.

## Location Facts

Primary new-company location:
- Location code: `MUDMIXER-HQ-NEW`
- Description: `MudMixer HQ - NEW`
- Effective date: `2026-03-01`
- Address:
  - `MudMixer, LLC`
  - `3401 Mallory Ln Ste 100`
  - `Franklin, TN 37067-2026`
- Use this location/address for new return setups unless a state-specific exception is known.

Important open item:
- Avalara still shows additional information required for the Franklin location because Tennessee `LOCATION I.D. NUMBER` is blank.
- Do not invent this number.

## Current Project Status

Delete/expire phase:
- Delete candidates were worked through and expired from the old company.
- Indiana was handled manually by the user and should be treated as done for progress-tracking purposes.

Migration/setup phase:
- Work one return at a time unless the user explicitly asks for batch work.
- After each substantive setup action, stop and summarize what was done so the user can review.
- Registration-driven setup progress is tracked in `registration-statuses-migration-focus.csv`.

Configured new-company returns so far:
- Alabama `USAL2620`
  - Filing request ID: `1864201`
  - Effective date: `2026-03-01`
- California `USCAPREPAYMENT`
  - Filing request ID: `1864226`
  - Effective date: `2026-04-01`
- California `USCACDTFA401A2`
  - Filing request ID: `1864243`
  - Effective date: `2026-01-01`
- Nebraska `USNEFORM10COMBINED`
  - Filing cadence saved as `Monthly`
  - First filing period: `March 2026, filed in April`
- North Carolina `USNCE500E536`
  - Filing cadence saved as `Monthly`
  - First filing period: `March 2026, filed in April`

Note on Avalara status:
- Newly configured returns may appear in `filingRequests` as `ChangeRequest` / `In review` before they appear as active filing calendars.

## California-Specific Learnings

- California is not fully configured with only `USCAPREPAYMENT`.
- Avalara metadata shows `USCAPREPAYMENT` has a required dependency on `USCACDTFA401A2`.
- For this business, California inventory held in a 3PL is enough to keep California filing obligations relevant even without California employees or offices.
- For the California quarterly return setup, answer the Schedule C / in-state-locations question as `No` when there are no registered in-state locations.
- For California setup questions, distinguish carefully between:
  - inventory held in a California 3PL: `yes`
  - California employees/offices/company-run locations: `no`, unless the user says otherwise

California settings already used:
- Registration ID: `225031840`
- Username: `RedDogMS`
- EIN: `47-3097812`
- Mailing address for new-company California setups: Franklin, TN location above

## Nebraska-Specific Learnings

- Nebraska filing credentials are not the same as the registration portal credentials.
- Verified working Nebraska filing setup values:
  - Nebraska ID / account number: `16008693`
  - filing login user ID: `16008693`
  - filing login password / PIN: `27191`
- Nebraska e-pay setup is separate:
  - first e-pay login uses Nebraska ID as both username and password
  - the portal immediately forces creation of a new e-pay password
  - working e-pay password now set: `!MudMixer3031`
- Nebraska return setup also needs the e-pay ID number:
  - use `16008693`
- Nebraska return answers already used successfully:
  - no licensed locations in Nebraska
  - monthly filing
  - first filing period `March 2026, filed in April`
- Nebraska e-pay security questions were changed from the registration packet values and are now:
  - `What was the model of your first car? = Nimbus2000`
  - `What was the name of your first school? = Hogwarts`
  - `What was the name of your childhood best friend? = RonWeasley`

## North Carolina-Specific Learnings

- Avalara's official North Carolina setup guide says:
  - username: `state registration ID or FEIN`
  - password: `PIN or company name as registered, including spaces and special characters`
- The guide also says that if Avalara shows an invalid-credentials message for North Carolina, ignore it and save anyway.
- Direct North Carolina DOR online filing confirmed:
  - account ID `601709175` resolves to `MUDMIXER, LLC`
  - the state flow uses account ID plus FEIN and does not require a portal username
- New-company North Carolina setup was saved with:
  - form `NC E500 E536`
  - account ID `601709175`
  - username `601709175`
  - password `MUDMIXER, LLC`
  - no prepayment
  - monthly filing starting `March 2026`

## Michigan-Specific Learnings

- Michigan portal user created:
  - MiLogin profile: `MeltonW3031`
  - email: `v-fusrjqf@compliance.avalara.com`
- Michigan business relationship facts already verified:
  - FEIN `47-3097812` is accepted by MTO
  - NAICS code to use: `333100`
  - fiscal year end month: `December`
  - business phone for the MTO relationship step: `806-515-4683`
- Current blocker:
  - when re-entering the Michigan Treasury Online relationship flow for `MudMixer, LLC`, the portal returned `This business is currently locked. Please try back later.`
  - no treasury number or access code has been retrieved yet
  - no Michigan filing request has been created in Avalara

## Oklahoma-Specific Learnings

- Oklahoma registration confirmation alone is not enough to create the OkTAP login.
- OkTAP account creation requires a previously assigned `13-character` Oklahoma account ID plus:
  - FEIN
  - email
  - zip code on file with the Oklahoma Tax Commission
- If only the confirmation number is available and the 13-character account ID has not been received from the state, Oklahoma return setup remains blocked.

## 2FA Learnings

- Company-level Avalara 2FA alias for the new company currently resolves to:
  - alias: `v-fusrjqf@compliance.avalara.com`
  - destination: `finance@mudmixer.com`
- If 2FA is required during setup, the user will provide the code manually.
- Do not assume every state portal is already using the new alias; some legacy state accounts may still route MFA or access to old-entity / old-alias records.

## Useful Verified Technical Notes

Browser/API:
- In the authenticated Avalara browser session, `fetch('/api/token/returns')` returns a usable Returns API bearer token.
- Useful endpoints already verified:
  - `GET /companies/<companyId>/filingRequests`
  - `GET /companies/<companyId>/filingCalendars`
  - `GET /companies/<companyId>/companyAddresses`
  - `GET /filingCalendarMetadata?taxFormCode=<code>`

Return creation:
- A verified create endpoint is:
  - `POST /accounts/2006428542/companies/<companyId>/taxFormCode/<taxFormCode>/filingRequests/add`
- This has already been used successfully for California setup in the new company.

## Local Files

Useful workspace files:
- `return-action-review.csv`
- `return-action-delete.csv`
- `return-action-migrate.csv`
- `active-returns-default-company-code.csv`
- `active-returns-default-company-code-wide.csv`
- `active-returns-default-company-code.json`

Sensitive data note:
- Unmasked passwords were exported into the working CSV data.
- Do not re-copy those secrets into AGENTS notes unless the user explicitly asks.
