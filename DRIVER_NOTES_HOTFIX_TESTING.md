# Driver Recent Notes hotfix

## Changes

- Driver-facing timestamps are converted from stored UTC to the portal timezone.
- Dates display as `dd/mm/yyyy HH:mm` using 24-hour time.
- The default portal timezone is `Pacific/Auckland` and can be changed with the `PORTAL_TIMEZONE` environment variable.
- Drivers can remove their own items from Recent Notes.
- Items more than 24 hours old are automatically removed from the driver's Recent Notes view.
- Removing an item from Recent Notes does not delete the Dispatch operational record.

## Tests

1. Send a request and confirm the displayed date is `dd/mm/yyyy` and the time matches local time.
2. Add a visible acknowledgement/comment from Dispatch and confirm its time is also local.
3. Press **Delete** on the driver item and confirm it disappears from Recent Notes.
4. Confirm the same request remains visible in the Dispatch dashboard.
5. Temporarily change a test request's `created_at` to more than 24 hours ago and confirm it disappears from the driver's Recent Notes.
6. Confirm acknowledgement and completion status updates still reach requests that remain visible.

## Render setting

For New Zealand local time, either leave the default unchanged or add:

`PORTAL_TIMEZONE=Pacific/Auckland`

For another region, use a valid IANA timezone name such as `Australia/Sydney`.
