# AOI Desktop call capture

AOI now has a standalone desktop recorder alongside its Chrome extension and web
workspace. Keep AOI Desktop running, join a Microsoft Teams call, Slack huddle,
Discord call, or another desktop call, and use the recording shortcut. The call app
selector labels the session; it is **not** a per-application audio filter. System
loopback captures other apps and notifications too. Video tracks are stopped and
never encoded, displayed or uploaded.

The desktop app uses the existing live-session backend for transcription, task and
decision extraction, project continuity and workspace isolation. Its local WAV
backup does not depend on the backend. Enable the microphone to include your own
speech; system audio captures what the other participants play on your computer.

## Open a packaged build

- macOS: `desktop/dist/AOI Desktop-darwin-arm64/AOI Desktop.app` for Apple Silicon.
- Windows: extract the complete `AOI Desktop-win32-x64` build, then run
  `AOI Desktop.exe`. Keep its adjacent resources and DLLs with it.

These are development builds. Distribution signing/notarization needs the owner's
platform certificates; no signing identity is fabricated. macOS capture requires
14.2 or newer. Windows uses Electron's loopback path; native Windows call capture
has not been runtime-tested on this Mac.

1. Open **Connection & shortcut settings**. Set the same backend and dashboard URLs
   used by the workspace. Save changes before using **Check saved connection**. Remote
   URLs require HTTPS; localhost may use HTTP.
2. For development, use the backend API key if configured. For company workspaces,
   use a workspace access token (or supported workspace API key) and workspace ID.
   No credential is passed through dashboard links or WebSocket URLs. Saved tokens
   are encrypted with the operating-system credential facility.
3. Grant recording permissions when the OS asks. On macOS, allow AOI Desktop in
   **System Settings → Privacy & Security → Screen & System Audio Recording** and
   allow microphone access when including your microphone. Use the packaged `.app`
   so permissions belong to AOI rather than a development terminal/IDE.
4. Set the meeting title, optional project, listening language and optional names or
   technical terms. Check the audio level after recording begins. Headphones reduce
   speaker audio leaking back into the microphone. If a call is routed to another
   output device, check that the loopback signal actually contains it.
5. Press **⌘⇧Space** on macOS or **Ctrl⇧Space** on Windows to start. Press again to
   stop and finalize. The app must be running. The tray/menu-bar icon and recorder
   show a recording state; closing the window hides it without ending capture.
6. Use **View meeting notes** or **Open workspace** for the same transcript and tasks
   in the dashboard. If live finalization cannot be confirmed, inspect the workspace
   before repeating a capture/upload.

The shortcut is configurable and registration failure is reported. The app does
not start at login or begin recording automatically. A local recording can still
be made when the backend is offline if **Keep a local WAV recording** is enabled.
Files are saved in AOI's application-data `recordings` folder, with restrictive
permissions where supported. They remain there until removed; opening the folder
is an explicit action. A local recording is not automatically uploaded. The current
web upload limit is 100 MB, so long WAV recordings need splitting or a different
supported ingest path rather than assuming every WAV fits.

## Build and run from source

Requires Node 22.12+ and the existing frontend fonts in this repository:

```sh
cd desktop
npm ci
npm start
```

Build platform folders with:

```sh
npm run package:mac
npm run package:win
```

The macOS build uses the host architecture; `npm run package:mac -- x64` produces
an Intel build. Windows defaults to x64. Packaging copies licensed fonts and supplies
macOS audio/microphone usage descriptions. This packages the recorder; it does not
package or start the backend. Apply migration `0011_transcription_quality` with the
usual `alembic upgrade head` or Docker startup flow before using updated live APIs.

## Audio and reconnect behavior

The audio graph mixes digital system audio with an optional echo-cancelled/noise-
suppressed microphone. It converts directly to mono PCM16 at the backend's reported
16/24/48 kHz rate, without MP3 encoding. AudioWorklet batches are flushed before stopping;
the WAV writer handles partial filesystem writes and patches the final header.
The level meter reports the incoming signal, with a warning for clipping or silence.

Audio is sent only after the backend's ready message. Authentication travels in a
WebSocket auth frame; workspace routing uses a header. Up to 30 seconds of PCM is
buffered while reconnecting, preserving order. If that buffer is exceeded or access
is denied, the failure is visible; a configured local WAV continues to preserve the
recording. With local backup off, access denial or buffer overflow stops capture with an explicit
error. An unconfigured speech provider is rejected when local backup is off.
Do not mistake 'recording locally' for live transcription.

Desktop reconnects preserve the same live transcript. Single-process development
uses a per-session writer lock; OIDC deployments use the existing Redis lease.
Busy connections close with retryable code 1013. The previous consumer finishes
before the next connection seeds its transcript. Explicit stop is still required
to finalize; an interrupted session may remain live until it is resumed/finalized.

## Verification

`npm test` runs configuration, authentication, WAV ordering, partial-write,
backpressure, queue-bound and AudioWorklet tail/clipping tests. `npm run test:ui`
launches the real Electron runtime on the host, injects a synthetic audio source,
and checks local WAV byte counts, WebSocket delivery/finalization, renderer state
and OS shortcut registration. It also verifies denied permission creates no session,
offline WAV capture works, and missing-backup failures remain visible. It requests no system audio/microphone permission and
records no real call. OS delivery of a physical hotkey and native Teams/Slack/Discord
capture need a real-device acceptance check after granting permissions.

The same synthetic suite also passes against the packaged Apple Silicon app using
`AOI_DESKTOP_EXECUTABLE` set to its `Contents/MacOS/AOI Desktop` executable.
Both macOS and Windows packages are generated. Building the Windows executable on
macOS is not a Windows runtime test.

The implementation follows Electron's [desktop capture](https://www.electronjs.org/docs/latest/api/desktop-capturer),
[global shortcuts](https://www.electronjs.org/docs/latest/api/global-shortcut), and
[secure credential storage](https://www.electronjs.org/docs/latest/api/safe-storage) APIs.
