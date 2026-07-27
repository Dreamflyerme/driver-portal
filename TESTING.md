# Coordinated build testing checklist

Use a copy of production data where possible. The application migrates the SQLite schema automatically on first start.

## Access and passwords

1. Try an unknown username and confirm the message remains generic.
2. Try a valid username with a wrong password and confirm it says **Incorrect password**.
3. Confirm pending, rejected, inactive and disabled accounts receive the appropriate message.
4. Reset a driver password as Admin and as Depot, log in with the temporary password, and confirm the user must change it before accessing the app.
5. Confirm a normal self-service password change does not trigger another forced change.

## Driver management

1. Change a driver's Home Depot in Admin, save, and confirm the driver appears under the new depot.
2. Approve pending drivers from both Admin and Depot screens and confirm all pending counters clear immediately.
3. Search drivers by name and driver number in Admin and Depot. Confirm Depot results stay within assigned depots.
4. Confirm Depot users cannot move drivers to depots they do not manage.

## Driver workflow

1. Save shift details with **Remember these details for future logins** selected.
2. Log out and back in and confirm driver name, depot and truck are prefilled.
3. Confirm supply-number fields open a numeric keypad on a mobile device and reject non-numeric characters.

## Dispatch

1. Assign a Default Desk Profile to a dispatcher in Admin.
2. Log in as that dispatcher and confirm the profile loads automatically.
3. Temporarily switch profiles and confirm the assigned default remains unchanged on the next fresh login.
4. Assign **None** and confirm Dispatch opens safely without a forced profile.
5. Delete an assigned profile and confirm affected users fall back safely.
6. Check profile, queue/status/depot/search combinations for clear and predictable results.

## Page state and branding

1. Expand several Admin sections, edit and save one record, and confirm the page returns to the same position, keeps other sections expanded, and collapses the edited record.
2. Confirm the Fonterra logo appears on login and in headers across Driver, Dispatch, Admin and Depot pages.
3. Confirm the browser favicon appears where supported.
