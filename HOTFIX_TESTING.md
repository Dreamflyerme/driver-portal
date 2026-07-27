# Driver Request Hotfix Test

1. Log in as a driver and enter shift details.
2. Send each request type at least once.
3. Confirm the green **Sent to Dispatch** screen appears.
4. Confirm the page returns to the main request menu automatically.
5. Confirm the request appears immediately under **Recent notes**.
6. Open Dispatch and confirm the request appears once only.
7. For a request type configured for acknowledgement, click **Acknowledge**.
8. Within five seconds, confirm the driver's status changes to **Acknowledged**.
9. For a request type configured for acknowledgement and completion, click **Done**.
10. Within five seconds, confirm the driver's status changes to **Done**.
11. Refresh the driver page and confirm request history remains present.
12. Test a temporarily disconnected browser: the driver must see a failure and no duplicate request should be created by a single click.
