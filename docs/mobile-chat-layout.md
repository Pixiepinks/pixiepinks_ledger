# Compact mobile conversation view

At widths of 700px or less, opening a conversation uses a dedicated chat layout:
- One 54px header: back, single-line ellipsized customer name/phone, conversation
  mode badge and Chat options (three dots).
- The general WhatsApp page header and hamburger are hidden while chatting. Back
  restores the header and conversation list.
- Chat options contains the existing Take Over/return action, Team notes, Details,
  owner assignment and lead-status controls, subject to their existing permissions.
- The composer uses one row: +, attachment, expandable message field, microphone
  and Send. Extra actions (Saved Voices, Quick Messages, Product, Create Order)
  are available in the + dialog. Text grows to a bounded height while typing.
- History takes the remaining height. Visual viewport resize events adjust the
  chat height when the available screen shrinks, such as for a mobile keyboard.

The same original controls are moved into the mobile dialogs, not cloned. Their
IDs, event listeners, hidden/disabled states, CSRF and server authorization remain
unchanged. Desktop restores the controls to their original positions. Opening
another existing action dialog first closes the mobile menu. Escape/Close and
conversation-access revocation close the dialogs. Recording, previews and pending
attachment controls retain the existing workflow.

No messaging, voice encoding, webhook, assignment or AI settings logic was changed.
Global AI remains OFF. Browser sends in automated checks are mocked only.

## Deployment and rollback

Deploy the reviewed commit normally and reload Inbox assets. No migration,
dependency or setting change is required. On a test account, verify mobile chat
header, menus, input expansion, keyboard opening/closing and Back navigation, then
verify desktop controls. Check an owner and a read-only shared conversation.
Do not send live messages as part of deployment verification without authorization.

Rollback restores the preceding application version and reloads assets. No database
rollback is needed. Keep existing Global AI and production voice settings intact.

Browser checks cover 320/360/390/430px widths, long customer names, history space,
short keyboard-sized viewports, nested action dialogs, mocked text sending,
attachment category controls, read-only sharing and restoration at desktop width.
Actual on-device keyboard behavior should also be checked after deployment.

## Validation

153 automated checks passed across mobile layout, Inbox, private notes, lead
editing, voice staging, Saved Voices, attachments, customer details, filters and
collection sends; 5 optional integration checks were skipped. Existing mobile
browser tests now open the management/action menus before using moved controls.
No live customer messages or production setting changes were performed.
