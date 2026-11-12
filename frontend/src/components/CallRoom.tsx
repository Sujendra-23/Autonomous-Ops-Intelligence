import { useCallback, useEffect, useMemo, useState } from "react";
import {
  LiveKitRoom,
  VideoConference,
  useConnectionState,
  useParticipants,
  useLocalParticipant,
} from "@livekit/components-react";
import { ConnectionState } from "livekit-client";
import "@livekit/components-styles";
import { api } from "../api";
import type { Navigate } from "../navigation";
import { usePollingData } from "../usePollingData";
import { Icon } from "./Icon";
import { meetingError, meetingTime } from "./MeetingScheduling";
import "./calls.css";

// The SDK's compact control bar uses icon-only buttons. Supply explicit names
// as its responsive layout and publication state change.
function MediaControlLabels() {
  const { isMicrophoneEnabled, isCameraEnabled, isScreenShareEnabled } =
    useLocalParticipant();
  useEffect(() => {
    const root = document.querySelector(".call-stage");
    if (!root) return;
    const label = () => {
      const controls: [string, string][] = [
        [".lk-disconnect-button", "Leave"],
        [".lk-chat-toggle", "Chat"],
        [".lk-chat .lk-close-button", "Close chat"],
        [
          'button[data-lk-source="microphone"]',
          isMicrophoneEnabled ? "Mute microphone" : "Unmute microphone",
        ],
        [
          'button[data-lk-source="camera"]',
          isCameraEnabled ? "Turn camera off" : "Turn camera on",
        ],
        [
          'button[data-lk-source="screen_share"]',
          isScreenShareEnabled ? "Stop screen share" : "Share screen",
        ],
      ];
      for (const [selector, name] of controls)
        root
          .querySelectorAll(selector)
          .forEach((button) => button.setAttribute("aria-label", name));
    };
    label();
    const observer = new MutationObserver(label);
    observer.observe(root, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [isMicrophoneEnabled, isCameraEnabled, isScreenShareEnabled]);
  return null;
}
function People() {
  const participants = useParticipants();
  const connection = useConnectionState();
  return (
    <div className="call-room-status">
      <span role="status">
        {connection === ConnectionState.Connected
          ? "Connected"
          : connection === ConnectionState.Reconnecting
            ? "Reconnecting…"
            : "Connecting…"}
      </span>
      <details>
        <summary>People ({participants.length})</summary>
        <ul>
          {participants.map((person) => (
            <li key={person.identity}>
              {person.name || "Participant"}
              {person.isLocal ? " (you)" : ""}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
export default function CallRoom({
  id,
  onNavigate,
}: {
  id: string;
  onNavigate: Navigate;
}) {
  const load = useCallback(
    (signal: AbortSignal) => api.aoiMeeting(id, signal),
    [id],
  );
  const { data, error: loadError, refresh } = usePollingData(load, 30000);
  const [session, setSession] = useState<{
    token: string;
    server_url: string;
    audio: boolean;
    camera: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [name, setName] = useState("");
  const [audio, setAudio] = useState(true);
  const [camera, setCamera] = useState(false);
  const options = useMemo(() => ({ adaptiveStream: true, dynacast: true }), []);
  async function join() {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.joinAoiMeeting(id, name.trim());
      setSession({
        ...result,
        audio,
        camera: data?.kind === "meeting" && camera,
      });
    } catch (cause) {
      setError(meetingError(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="workspace-page aoi-call-page">
      <button
        className="text-button back-link"
        onClick={() => onNavigate("meetings", undefined, data?.kind === "huddle" ? "huddles" : "upcoming")}
      >
        <Icon name="arrow" />
        All meetings
      </button>
      {loadError && !data && (
        <div className="load-error" role="alert">
          <p>
            Couldn’t open this meeting. Check your connection and workspace
            access.
          </p>
          <button className="ghost" onClick={refresh}>
            Try again
          </button>
        </div>
      )}
      {!data && !loadError && (
        <p role="status" className="muted">
          Opening meeting…
        </p>
      )}
      {data && (
        <>
          <header className="page-heading">
            <div>
              <h2>{data.title}</h2>
              <p className="muted">
                {data.kind === "huddle"
                  ? "Audio huddle · Drop in with your team"
                  : meetingTime(data)}
              </p>
            </div>
            <button
              className="ghost"
              onClick={() =>
                void navigator.clipboard
                  .writeText(
                    `${window.location.origin}/meetings/rooms/${data.id}`,
                  )
                  .then(() => setNotice("Meeting link copied."))
                  .catch(() =>
                    setError(
                      "Couldn’t copy. Copy this page’s address to share it.",
                    ),
                  )
              }
            >
              Copy meeting link
            </button>
          </header>
          {notice && (
            <p role="status" className="muted">
              {notice}
            </p>
          )}
          {error && (
            <p role="alert" className="load-error">
              {error}
            </p>
          )}
          {session ? (
            <div className="call-stage" data-lk-theme="default">
              <LiveKitRoom
                token={session.token}
                serverUrl={session.server_url}
                connect
                audio={session.audio}
                video={session.camera}
                options={options}
                onError={() =>
                  setError(
                    "Couldn’t complete the call connection. Check your network, camera and microphone permissions.",
                  )
                }
                onMediaDeviceFailure={() =>
                  setError(
                    "A camera or microphone could not be opened. Check browser permissions or use another device.",
                  )
                }
                onDisconnected={() => {
                  setSession(null);
                  setNotice("You left the call. You can rejoin below.");
                }}
              >
                <MediaControlLabels />
                <People />
                <VideoConference />
              </LiveKitRoom>
            </div>
          ) : (
            <section className="call-prejoin" aria-labelledby="join-heading">
              <div>
                <Icon name={data.kind === "huddle" ? "mic" : "meetings"} />
                <h3 id="join-heading">Ready to join?</h3>
                <p className="muted">
                  {data.kind === "huddle"
                    ? "Cameras stay off. Share your screen whenever it helps."
                    : "Choose your microphone and camera. You can change them during the call."}
                </p>
                {data.description && (
                  <p className="call-agenda">{data.description}</p>
                )}
              </div>
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  void join();
                }}
              >
                <label htmlFor="call-display-name">Your name</label>
                <input
                  id="call-display-name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  required
                  maxLength={80}
                  autoComplete="name"
                  disabled={busy}
                />
                <label className="call-device-toggle">
                  <input
                    type="checkbox"
                    checked={audio}
                    onChange={(event) => setAudio(event.target.checked)}
                    disabled={busy}
                  />
                  Microphone on
                </label>
                {data.kind === "meeting" && (
                  <label className="call-device-toggle">
                    <input
                      type="checkbox"
                      checked={camera}
                      onChange={(event) => setCamera(event.target.checked)}
                      disabled={busy}
                    />
                    Camera on
                  </label>
                )}
                <button
                  className="primary"
                  disabled={busy || !data.calls_enabled}
                >
                  {busy
                    ? "Joining…"
                    : `Join ${data.kind === "huddle" ? "huddle" : "meeting"}`}
                </button>
                {!data.calls_enabled && (
                  <p className="schedule-help">
                    Calling is not connected. Ask your administrator to enable
                    AOI calls.
                  </p>
                )}
              </form>
            </section>
          )}
        </>
      )}
    </div>
  );
}
