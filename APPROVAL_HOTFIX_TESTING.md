# Driver approval hotfix testing

This hotfix corrects the Admin **Make active** action for pending drivers.

## Test

1. Register a new driver through **Request access**.
2. Open the driver in **Admin**.
3. Click **Make active**.
4. Confirm the message says **Driver activated and approved**.
5. Confirm the driver no longer appears as pending.
6. Log in using the driver's credentials.
7. Confirm login succeeds rather than showing the depot approval message.
8. Make the driver inactive and confirm login is blocked as inactive.
9. Make the driver active again and confirm login succeeds.

The depot **Approve** action continues to work as before.
