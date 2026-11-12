import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, type AoiMeeting } from "../api";
import { Icon } from "./Icon";
import "./meeting-scheduling.css";

export function meetingError(error: unknown) {
  if (error instanceof Error) {
    try {
      const detail = JSON.parse(
        error.message.slice(error.message.indexOf("{")),
      ).detail;
      if (typeof detail === "string") return detail;
      if (Array.isArray(detail))
        return detail
          .map((item) => String(item.msg).replace(/^Value error, /, ""))
          .join(". ");
    } catch {
      /* A lost response may follow a successful write. */
    }
  }
  return "Couldn’t confirm the meeting. Refresh the list or retry with the same details.";
}
export function meetingTime(event: AoiMeeting) {
  const date = new Date(event.starts_at);
  if (!Number.isFinite(date.getTime())) return "Time unavailable";
  try {
    return (
      new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
        timeZone: event.time_zone || undefined,
      }).format(date) + ` · ${event.time_zone.replaceAll("_", " ")}`
    );
  } catch {
    return date.toLocaleString();
  }
}
function initialTime() {
  const next = new Date(
    Math.ceil((Date.now() + 15 * 60000) / 1800000) * 1800000,
  );
  return {
    date: `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`,
    time: `${String(next.getHours()).padStart(2, "0")}:${String(next.getMinutes()).padStart(2, "0")}`,
  };
}
export function MeetingScheduling({
  calendarEnabled,
  onClose,
  onScheduled,
}: {
  calendarEnabled: boolean;
  onClose: () => void;
  onScheduled: (meeting: AoiMeeting) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(crypto.randomUUID());
  const submitting = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const [defaults] = useState(initialTime);
  const [zone, setZone] = useState(
    () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
  );
  const [calendarInvite, setCalendarInvite] = useState(false);
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const form = new FormData(event.currentTarget);
    submitting.current = true;
    setBusy(true);
    setError("");
    try {
      const meeting = await api.scheduleAoiMeeting({
        request_id: requestId.current,
        title: String(form.get("title")).trim(),
        meeting_date: String(form.get("date")),
        start_time: String(form.get("time")),
        time_zone: String(form.get("time-zone")),
        duration_minutes: Number(form.get("duration")),
        attendees: String(form.get("guests") || "")
          .split(/[,;\n]/)
          .map((value) => value.trim())
          .filter(Boolean),
        description: String(form.get("description") || ""),
        reminder_minutes:
          form.get("reminder") === "none" ? null : Number(form.get("reminder")),
        invite_on_calendar: calendarInvite,
      });
      onScheduled(meeting);
    } catch (cause) {
      setError(meetingError(cause));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="meeting-composer" aria-labelledby="schedule-heading">
      <div className="section-heading">
        <h3 id="schedule-heading" tabIndex={-1} ref={heading}>
          Schedule a meeting
        </h3>
        <button className="text-button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
      </div>
      <form onSubmit={(event) => void submit(event)}>
        <fieldset disabled={busy}>
          <label htmlFor="schedule-title">Meeting title</label>
          <input
            id="schedule-title"
            name="title"
            required
            maxLength={512}
            placeholder="e.g. Project planning"
            autoComplete="off"
          />
          <div className="schedule-time-fields">
            <div>
              <label htmlFor="schedule-date">Date</label>
              <input
                id="schedule-date"
                name="date"
                type="date"
                defaultValue={defaults.date}
                required
              />
            </div>
            <div>
              <label htmlFor="schedule-time">Start time</label>
              <input
                id="schedule-time"
                name="time"
                type="time"
                defaultValue={defaults.time}
                required
              />
            </div>
            <div>
              <label htmlFor="schedule-duration">Duration</label>
              <select id="schedule-duration" name="duration" defaultValue="30">
                <option value="15">15 minutes</option>
                <option value="30">30 minutes</option>
                <option value="45">45 minutes</option>
                <option value="60">1 hour</option>
                <option value="90">1.5 hours</option>
                <option value="120">2 hours</option>
              </select>
            </div>
          </div>
          <p className="schedule-zone-caption">
            Times in {zone.replaceAll("_", " ")}. Change the time zone in More
            options.
          </p>
          {calendarEnabled && (
            <label className="schedule-invite-toggle">
              <input
                type="checkbox"
                checked={calendarInvite}
                onChange={(event) => setCalendarInvite(event.target.checked)}
              />
              Send Google Calendar invitations
            </label>
          )}
          {calendarInvite && (
            <>
              <label htmlFor="schedule-guests">Teammate emails</label>
              <textarea
                id="schedule-guests"
                name="guests"
                rows={2}
                maxLength={25600}
                placeholder="alex@company.com, sam@company.com"
                aria-describedby="schedule-guest-help"
              />
              <p className="schedule-help" id="schedule-guest-help">
                Separate emails with commas. Invitees need access to this AOI
                workspace.
              </p>
            </>
          )}
          <details className="schedule-options">
            <summary>More options</summary>
            <label htmlFor="schedule-zone">Time zone</label>
            <input
              id="schedule-zone"
              name="time-zone"
              list="meeting-time-zones"
              value={zone}
              onChange={(event) => setZone(event.target.value)}
              required
            />
            <datalist id="meeting-time-zones">
              {[
                ...new Set([
                  zone,
                  "UTC",
                  "America/Los_Angeles",
                  "America/New_York",
                  "Europe/London",
                  "Europe/Paris",
                  "Asia/Kolkata",
                  "Asia/Tokyo",
                  "Australia/Sydney",
                ]),
              ].map((value) => (
                <option key={value} value={value} />
              ))}
            </datalist>
            <label htmlFor="schedule-description">
              Notes <span className="muted">(optional)</span>
            </label>
            <textarea
              id="schedule-description"
              name="description"
              rows={3}
              maxLength={10000}
            />
            <label htmlFor="schedule-reminder">Calendar reminder</label>
            <select id="schedule-reminder" name="reminder" defaultValue="10">
              <option value="none">No reminder</option>
              <option value="5">5 minutes before</option>
              <option value="10">10 minutes before</option>
              <option value="15">15 minutes before</option>
              <option value="30">30 minutes before</option>
              <option value="60">1 hour before</option>
            </select>
            <p className="schedule-help">
              Reminders apply when calendar invitations are enabled.
            </p>
          </details>
          <div className="schedule-submit">
            <span>
              <Icon name="meetings" />
              Your meeting happens inside AOI.
            </span>
            <button className="primary" type="submit">
              {busy
                ? "Scheduling…"
                : calendarInvite
                  ? "Schedule & invite"
                  : "Schedule meeting"}
            </button>
          </div>
        </fieldset>
      </form>
      {error && (
        <p className="load-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
