import type { Navigate } from "../navigation";
import { Icon } from "./Icon";

export function ExtensionSetup({ onNavigate }: { onNavigate: Navigate }) {
  return (
    <div className="workspace-page extension-setup">
      <header className="page-heading">
        <div>
          <h2>
            Your calls. <em>One clear memory.</em>
          </h2>
          <p className="muted">
            Record desktop calls with AOI Desktop, or capture a meeting tab with
            the browser extension.
          </p>
        </div>
      </header>
      <section className="panel desktop-capture-setup">
        <div className="section-heading">
          <h3>
            <Icon name="mic" />
            Record desktop calls
          </h3>
          <span className="capture-platforms">macOS · Windows</span>
        </div>
        <p className="muted">
          For Microsoft Teams, Slack huddles, Discord calls, and other desktop
          audio.
        </p>
        <ol className="setup-steps">
          <li>
            <h4>Open AOI Desktop</h4>
            <p>
              Open your AOI Desktop app build: <strong>.app on macOS</strong> or{" "}
              <strong>.exe on Windows</strong>. Keep the app running in the menu
              bar or system tray.
            </p>
            <details>
              <summary>Run from this project</summary>
              <p>
                In the <code>desktop/</code> directory, run{" "}
                <code>npm install</code> and <code>npm start</code>. Packaged
                builds are produced by <code>npm run package:mac</code> and{" "}
                <code>npm run package:win</code>.
              </p>
            </details>
          </li>
          <li>
            <h4>Connect the same workspace</h4>
            <p>
              In the desktop app’s connection settings, use this workspace’s
              backend, API key or access token, and workspace ID when required.
              Set the dashboard URL to <code>{window.location.origin}</code>.
            </p>
          </li>
          <li>
            <h4>Allow audio and choose what to include</h4>
            <p>
              Allow system audio recording and microphone access when your
              operating system asks. Enable{" "}
              <strong>Include my microphone</strong> to capture your side of the
              conversation. System audio capture includes sounds from all apps,
              including notifications.
            </p>
            <p className="muted">
              The macOS capture path needs macOS 14.2 or newer. Use the packaged
              app so recording permissions belong to AOI Desktop.
            </p>
          </li>
          <li>
            <h4>Press a key to start. Press again to finish.</h4>
            <p>
              <kbd>⌘ ⇧ Space</kbd> on Mac or <kbd>Ctrl ⇧ Space</kbd> on Windows
              toggles recording while your call app has focus. You can change
              the shortcut in settings. A visible recording state and tray
              indicator show when capture is active.
            </p>
          </li>
          <li>
            <h4>Give the transcript the best input</h4>
            <p>
              Use headphones, check the audio meter, choose the spoken language
              when you know it, and add names or technical terms. Keep the local
              WAV backup for reviewing important details. Live notes use the
              configured speech provider; no transcription engine can guarantee
              every word.
            </p>
            <p className="muted">
              If the backend is offline, the desktop app can keep recording
              locally with audio backup enabled. Reconnect to use live
              transcription. Local files are not uploaded automatically.
            </p>
            <button className="primary" onClick={() => onNavigate("meetings")}>
              Open captured meetings
              <Icon name="arrow" />
            </button>
          </li>
        </ol>
      </section>
      <details className="panel browser-capture-setup">
        <summary>
          Capture a browser meeting instead
          <Icon name="extension" />
        </summary>
        <ol className="setup-steps">
          <li>
            <h4>Load the Chrome extension</h4>
            <p>
              Open <code>chrome://extensions</code>, turn on Developer mode,
              choose <strong>Load unpacked</strong>, and select this project’s{" "}
              <code>extension/</code> folder. Pin AOI to your toolbar.
            </p>
          </li>
          <li>
            <h4>Connect the same workspace</h4>
            <p>
              In the extension’s connection settings, use the same backend and
              credentials. Set <strong>Dashboard URL</strong> to{" "}
              <code>{window.location.origin}</code>. Dashboard links do not
              transfer credentials.
            </p>
          </li>
          <li>
            <h4>Start on your meeting tab</h4>
            <p>
              Open the meeting or video, click the AOI toolbar icon, and choose{" "}
              <strong>Start capturing</strong>. The extension captures that
              tab’s audio. Choose <strong>Stop &amp; finalize</strong> when
              you’re done.
            </p>
          </li>
          <li>
            <h4>Continue in the dashboard</h4>
            <p>
              Choose <strong>View meeting in dashboard</strong> in the extension
              to open its notes, transcript, and follow-up tasks.
            </p>
          </li>
        </ol>
      </details>
      <section className="setup-alternative">
        <h3>Already have a recording or transcript?</h3>
        <p className="muted">
          Upload it to the same workspace. Audio uploads accept files up to 100
          MB.
        </p>
        <button className="ghost" onClick={() => onNavigate("upload")}>
          Upload a recording or transcript
          <Icon name="upload" />
        </button>
      </section>
    </div>
  );
}
