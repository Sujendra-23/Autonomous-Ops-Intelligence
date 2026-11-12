import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, type AoiMeeting } from "../api";
import type { Navigate } from "../navigation";
import { usePollingData } from "../usePollingData";
import { Icon } from "./Icon";
import {
  MeetingScheduling,
  meetingError,
  meetingTime,
} from "./MeetingScheduling";

export function RoomActions({
  meeting,
  onNavigate,
}: {
  meeting: AoiMeeting;
  onNavigate: Navigate;
}) {
  const [message, setMessage] = useState("");
  return (
    <div className="scheduled-actions">
      <button
        className="primary"
        onClick={() => onNavigate("meetings", `room:${meeting.id}`)}
      >
        Join {meeting.kind === "huddle" ? "huddle" : "meeting"}
        <Icon name="arrow" />
      </button>
      <button
        className="text-button"
        onClick={() =>
          void navigator.clipboard
            .writeText(`${window.location.origin}/meetings/rooms/${meeting.id}`)
            .then(() => setMessage("Meeting link copied."))
            .catch(() =>
              setMessage(
                "Couldn’t copy. Open the meeting and copy its address.",
              ),
            )
        }
      >
        Copy link
      </button>
      {message && (
        <span className="muted" role="status">
          {message}
        </span>
      )}
    </div>
  );
}
function Rooms({
  kind,
  onNavigate,
  revision,
}: {
  kind: "meeting" | "huddle";
  onNavigate: Navigate;
  revision: number;
}) {
  const [offset, setOffset] = useState(0);
  const load = useCallback(
    (signal: AbortSignal) => api.aoiMeetings(kind, offset, signal),
    [kind, offset, revision],
  );
  const { data, error, loading, refresh } = usePollingData(load, 15000);
  return (
    <section className="upcoming-meetings">
      <div className="section-heading">
        <h3>{kind === "huddle" ? "Team huddles" : "Upcoming meetings"}</h3>
        <button className="text-button" onClick={refresh} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {error && (
        <p className="load-error" role="alert">
          Couldn’t load meetings. Check the connection and try Refresh.
        </p>
      )}
      {!data && !error && (
        <p className="muted" role="status">
          Loading meetings…
        </p>
      )}
      {data && (
        <>
          {data.items.length ? (
            <ul className="scheduled-meeting-list">
              {data.items.map((meeting) => (
                <li key={meeting.id}>
                  <div className="scheduled-meeting-info">
                    <h4>
                      <Icon name={kind === "huddle" ? "mic" : "meetings"} />
                      {meeting.title}
                    </h4>
                    <p>
                      {kind === "huddle"
                        ? "Drop in whenever you need a conversation."
                        : meetingTime(meeting)}
                    </p>
                    {meeting.participants != null &&
                      meeting.participants > 0 && (
                        <span className="room-presence">
                          {meeting.participants} in the{" "}
                          {kind === "huddle" ? "huddle" : "meeting"}
                        </span>
                      )}
                  </div>
                  <RoomActions meeting={meeting} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          ) : (
            <div className="meeting-empty">
              <h4>
                {kind === "huddle"
                  ? "A quick conversation goes a long way."
                  : "Make room for your next conversation."}
              </h4>
              <p>
                {kind === "huddle"
                  ? "Start an audio huddle. Your teammates can drop in from here."
                  : "Schedule a meeting or start one now. Join without leaving AOI."}
              </p>
            </div>
          )}
          {(offset > 0 || data.has_more) && (
            <div className="meeting-pagination">
              <button
                className="ghost"
                disabled={!offset || loading}
                onClick={() => setOffset((value) => Math.max(0, value - 50))}
              >
                Previous
              </button>
              <button
                className="ghost"
                disabled={!data.has_more || loading}
                onClick={() => setOffset((value) => value + 50)}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
export function MeetingHub({
  onNavigate,
  children,
}: {
  onNavigate: Navigate;
  children: ReactNode;
}) {
  const [tab, setTab] = useState<"upcoming" | "huddles" | "captured">(
    "upcoming",
  );
  const [composer, setComposer] = useState<
    "schedule" | "meeting" | "huddle" | null
  >(null);
  const [status, setStatus] = useState<{
    calls_enabled: boolean;
    calendar_enabled: boolean;
  } | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [revision, setRevision] = useState(0);
  const [created, setCreated] = useState<AoiMeeting | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [inviteBusy, setInviteBusy] = useState(false);
  const requestId = useRef(crypto.randomUUID());
  const submitting = useRef(false);
  const quickTitle = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    void api
      .callStatus(controller.signal)
      .then(setStatus)
      .catch(() => {
        if (!controller.signal.aborted) setStatusError(true);
      });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (composer === "meeting" || composer === "huddle")
      quickTitle.current?.focus();
  }, [composer]);
  function open(value: "schedule" | "meeting" | "huddle") {
    requestId.current = crypto.randomUUID();
    setError("");
    setCreated(null);
    setComposer(value);
    setTab(value === "huddle" ? "huddles" : "upcoming");
  }
  async function start(form: HTMLFormElement) {
    if (submitting.current || !composer || composer === "schedule") return;
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const meeting = await api.startAoiMeeting({
        request_id: requestId.current,
        title: String(new FormData(form).get("title")).trim(),
        kind: composer,
      });
      onNavigate("meetings", `room:${meeting.id}`);
    } catch (cause) {
      setError(meetingError(cause));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <div className="workspace-page meetings-page">
      <header className="page-heading">
        <div>
          <h2>Meetings</h2>
          <p className="muted">Get together. Keep things moving.</p>
        </div>
        <div className="page-actions">
          <button
            id="schedule-meeting-trigger"
            className="ghost"
            onClick={() => open("schedule")}
          >
            Schedule
          </button>
          <button
            className="primary"
            onClick={() => open(tab === "huddles" ? "huddle" : "meeting")}
          >
            <Icon name={tab === "huddles" ? "mic" : "meetings"} />
            {tab === "huddles" ? "Start huddle" : "Start meeting"}
          </button>
        </div>
      </header>
      <div
        className="meetings-view-tabs"
        role="group"
        aria-label="Meeting views"
      >
        {(["upcoming", "huddles", "captured"] as const).map((value) => (
          <button
            key={value}
            aria-pressed={tab === value}
            disabled={busy}
            onClick={() => {
              setTab(value);
              setComposer(null);
            }}
          >
            {value.charAt(0).toUpperCase() + value.slice(1)}
          </button>
        ))}
      </div>
      {((status && !status.calls_enabled) || statusError) && (
        <p className="call-setup-notice" role="status">
          {statusError
            ? "Couldn’t check calling availability. Refresh the page to try again."
            : "In-app calls aren’t connected yet. Your administrator needs to enable calling. You can still schedule meetings."}
        </p>
      )}
      {composer === "schedule" && (
        <MeetingScheduling
          calendarEnabled={!!status?.calendar_enabled}
          onClose={() => {
            setComposer(null);
            document.getElementById("schedule-meeting-trigger")?.focus();
          }}
          onScheduled={(meeting) => {
            setCreated(meeting);
            setComposer(null);
            setRevision((value) => value + 1);
          }}
        />
      )}
      {(composer === "meeting" || composer === "huddle") && (
        <form
          className="quick-room-form"
          onSubmit={(event) => {
            event.preventDefault();
            void start(event.currentTarget);
          }}
        >
          <label htmlFor="quick-room-title">
            {composer === "huddle" ? "Huddle name" : "Meeting title"}
          </label>
          <input
            id="quick-room-title"
            ref={quickTitle}
            name="title"
            required
            maxLength={512}
            defaultValue={
              composer === "huddle" ? "Team huddle" : "Quick meeting"
            }
            disabled={busy}
          />
          <div>
            <button
              className="ghost"
              type="button"
              disabled={busy}
              onClick={() => setComposer(null)}
            >
              Cancel
            </button>
            <button
              className="primary"
              disabled={busy || !status?.calls_enabled}
            >
              {busy ? "Opening…" : "Continue"}
            </button>
          </div>
          {error && (
            <p role="alert" className="load-error">
              {error}
            </p>
          )}
        </form>
      )}
      {created && (
        <section
          className="scheduled-confirmation"
          aria-label="Scheduled meeting"
        >
          <p role="status">Meeting scheduled in AOI.</p>
          <h3>{created.title}</h3>
          <p className="muted">{meetingTime(created)}</p>
          {created.invitation_status === "requested" && (
            <p className="muted">Calendar invitations requested.</p>
          )}
          {["failed", "pending"].includes(created.invitation_status) && (
            <>
              <p className="load-error">
                Your meeting is saved. Calendar invitations haven’t been
                confirmed.
              </p>
              <button
                className="text-button"
                disabled={inviteBusy}
                onClick={() => {
                  setInviteBusy(true);
                  void api
                    .retryMeetingInvitations(created.id)
                    .then(setCreated)
                    .catch(() =>
                      setError(
                        "Couldn’t confirm invitations. Check Google Calendar before retrying.",
                      ),
                    )
                    .finally(() => setInviteBusy(false));
                }}
              >
                Retry calendar invitations
              </button>
              {error && (
                <p className="load-error" role="alert">
                  {error}
                </p>
              )}
            </>
          )}
          <RoomActions meeting={created} onNavigate={onNavigate} />
        </section>
      )}
      {tab === "captured" ? (
        <>
          <button
            className="text-button captured-upload"
            onClick={() => onNavigate("upload")}
          >
            Upload a transcript
            <Icon name="upload" />
          </button>
          {children}
        </>
      ) : (
        <Rooms
          key={tab}
          kind={tab === "huddles" ? "huddle" : "meeting"}
          onNavigate={onNavigate}
          revision={revision}
        />
      )}
    </div>
  );
}
