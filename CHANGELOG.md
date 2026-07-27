# Change log

## Refined driver and admin flow editor

### Driver experience

- Replaced the multi-field form with a guided, one-question-at-a-time flow.
- Added large mobile answer buttons and automatic advance for choice questions.
- Added back navigation, progress, review, sending, success, and retry states.
- Added duplicate-submit protection while a request is sending.
- Retained the existing recent-request list and live refresh.
- Added support for Yes/No, short text, number, longer note, and choice questions.

### Admin flow editor

- Hid editable technical field keys; question IDs now remain stable when labels change.
- Added creation and editing of conditional nested questions.
- Added question ordering controls.
- Added option duplication.
- Added expanded question types and a compact driver-flow preview.
- Preserved compatibility with existing JSON schemas and saved requests.

### Unchanged by design

- Dispatcher dashboard layout and workflow.
- Existing SQLite data and deployment configuration.
- Existing driver receipt modes.

## Testing

- Python syntax compilation passed.
- Updated smoke test suite passed: 2 tests.

## Driver navigation and depot-managed access

### Driver experience

- Added a persistent Main Menu button on the driver screen.
- Renamed the post-send action to Main Menu.
- Added a visible five-second countdown and automatic return to the main menu.

### Driver access requests

- Added Request access to the login screen.
- Drivers request access using driver number, name, home depot, and password.
- New requests are created inactive and remain pending until approved.
- Added approved, pending, and rejected access states.

### Depot role

- Added a Depot role managed by administrators.
- Administrators assign one or more depots to each Depot user.
- Depot users can only view, approve, or reject pending drivers for assigned depots.
- Administrators can review requests across all depots.

## Depot people management

- Expanded the Depot role from access approval to full driver-account management for assigned depots.
- Depot users can view active, inactive, rejected, and pending drivers in their depots.
- Added depot-scoped editing of driver name, driver number, and home depot.
- Added depot-scoped password resets, activation/deactivation, and deletion.
- Depot users cannot manage drivers outside their assigned depots or manage Admin, Dispatch, or Depot-role accounts.

## Admin page tidy-up

- Replaced fully expanded user forms with compact summary rows and per-user Manage panels.
- Moved password reset, status changes, deletion and depot assignment behind each user's expanded panel.
- Collapsed long depot lists by default and only show them for Depot-role users.
- Converted desk profiles, dashboard queues and depot routing into compact expandable records.
- Moved all create forms behind simple Add controls.
- Added visible record counts and clearer active, inactive and pending status badges.
- Kept all existing routes, permissions and stored data unchanged.

## Self-service password changes

- Added a Change password link for every signed-in role.
- Users must verify their current password before setting a new one.
- Added matching confirmation, minimum-length validation, and prevention of reusing the current password.
- Existing Admin and Depot password-reset controls remain available for account recovery.

## Admin user grouping
- Split the Admin user directory into collapsible Admin, Dispatch, Depot user, and Driver sections.
- Grouped drivers into collapsible home-depot sections, with a separate Unassigned depot group.
- Retained the existing per-user Manage controls inside each grouped section.

## Depot people tidy-up

- Reworked the Depot people page into collapsible depot groups.
- Driver management controls are hidden inside compact per-driver Manage panels.
- Added depot-level pending approval totals and quick links.
- Added an Admin pending-approvals summary showing counts by depot.
- Pending counts also appear beside the relevant driver depot group and depot routing row.

## 2026-07 consolidated bench build
- Added Fonterra branding to login and navigation.
- Added clearer inactive, pending and rejected login messages plus forgot-password guidance.
- Added forced password change after Admin/Depot temporary password resets.
- Added remembered driver shift defaults.
- Added numeric mobile keyboard and digit-only handling for supply numbers.
- Added driver search for Admin and Depot users.
- Added Admin controls for driver home depot and dispatcher default desk profile.
- Removed the global "Standard Default" desk-profile concept.
- Corrected pending approval summaries to count only inactive pending accounts.
- Preserved page scroll position after form saves and improved dispatch filter wording.

## Hotfix - Driver request submission and status tracking

- Fixed driver requests displaying “Could not send” after the request had already reached Dispatch.
- Driver fetch submissions now receive an explicit JSON success response instead of following a page redirect.
- Restored the success screen and automatic return to the main request menu.
- Added no-cache responses for the driver's live request history/status feed.
- Added response validation so HTML redirects or server errors cannot be mistaken for successful API responses.
- Driver request history now refreshes immediately after sending and continues polling for acknowledgement/completion changes.

## Driver approval hotfix

- Fixed Admin **Make active** leaving a driver in `pending` approval state.
- Activating a driver from Admin now also records the account as approved.
- Records the reviewing Admin and review time when activation grants approval.

## Driver Recent Notes hotfix

- Converted driver-visible request and comment timestamps from UTC to configurable local time.
- Standardised displayed dates as `dd/mm/yyyy HH:mm`.
- Added driver-controlled removal of requests from Recent Notes.
- Automatically hides Recent Notes items after 24 hours.
- Preserves Dispatch records when a driver removes or ages out an item.
