# Inbox voice playback speed

Every audio player in conversation history has a speed button cycling through
1×, 1.5×, 2× and 3×. The choice applies to the current history players and new
players loaded when changing chats or refreshing history. It is remembered in
this browser's local storage, not in customer records or a server-side account
setting. If browser storage is blocked or invalid, playback defaults to 1× and
the button still works for the current page.

Changing speed sets only the browser audio playback rate; it does not restart,
autoplay, re-encode, upload or modify recordings. Pitch preservation is enabled
where the browser supports it. Saved Voices library previews and recording
previews are unchanged. Keyboard buttons have descriptive labels and tooltips.

Deploy normally and reload Inbox assets. No migration, dependency, production
setting or AI toggle is required. Rollback restores the previous frontend version;
the saved browser preference is harmless if unused. Keep Global AI OFF and voice
encoding settings unchanged. No live messages were sent during implementation.

19 automated checks passed, including three new playback tests plus existing
Inbox and mobile layout checks. Coverage includes rate cycling while playing,
playhead continuity, no autoplay, pitch preservation, persistence across reload
and chat switches, dynamic history without duplicate controls, 320/360/390/430px
layouts, keyboard activation, invalid preferences and blocked browser storage.
