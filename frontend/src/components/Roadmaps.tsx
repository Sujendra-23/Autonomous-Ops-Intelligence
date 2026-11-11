import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  api,
  type Project,
  type ProjectRoadmap,
  type RoadmapDocument,
  type RoadmapInput,
  type RoadmapMilestone,
  type TranscriptSummary,
} from "../api";
import { Icon } from "./Icon";
import "./roadmaps.css";

function message(error: unknown) {
  if (error instanceof Error) {
    try {
      const detail = JSON.parse(
        error.message.slice(error.message.indexOf("{")),
      ).detail;
      if (typeof detail === "string") return detail;
    } catch {
      /* use recovery copy */
    }
  }
  return "Couldn’t complete this action. Check your connection and try again.";
}
const blank: RoadmapInput = {
  name: "",
  project_id: null,
  transcript_id: null,
  source_type: "manual",
  plan: "",
  horizon_weeks: 12,
  start_date: null,
};

export function Roadmaps() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [meetings, setMeetings] = useState<TranscriptSummary[]>([]);
  const [meetingTotal, setMeetingTotal] = useState(0);
  const [input, setInput] = useState<RoadmapInput>(blank);
  const [saved, setSaved] = useState<ProjectRoadmap | null>(null);
  const [document, setDocument] = useState<RoadmapDocument | null>(null);
  const [editing, setEditing] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"load" | "generate" | "save" | "">("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [createTasks, setCreateTasks] = useState(true);
  const [composerOpen, setComposerOpen] = useState(true);
  const activeRequest = useRef<AbortController | null>(null);
  const revision = useRef(0);
  const draftRef = useRef<HTMLElement>(null);
  useEffect(() => {
    void load();
    return () => {
      activeRequest.current?.abort();
      revision.current++;
    };
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [dirty]);
  async function load() {
    setBusy("load");
    setError("");
    try {
      const [list, recent] = await Promise.all([
        api.projects(),
        api.transcripts({ limit: 100 }),
      ]);
      setProjects(list);
      setMeetings(recent.items);
      setMeetingTotal(recent.total);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy("");
    }
  }
  async function chooseProject(id: string) {
    const current = ++revision.current;
    activeRequest.current?.abort();
    setSaved(null);
    setDocument(null);
    setEditing(false);
    setFeedback("");
    setError("");
    setComposerOpen(true);
    setInput({
      ...blank,
      project_id: id || null,
      name: projects.find((p) => p.id === id)?.name ?? "",
    });
    if (!id) return;
    setBusy("load");
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      const result = await api.projectRoadmap(id, controller.signal);
      if (current !== revision.current) return;
      setSaved(result);
      setDocument(result.roadmap);
      setComposerOpen(!result.roadmap);
      if (result.roadmap)
        setInput({
          name: result.name,
          project_id: id,
          source_type: result.roadmap.source_type,
          transcript_id:
            result.roadmap.source_type === "transcript"
              ? (result.roadmap.source_meeting_ids[0] ?? null)
              : null,
          plan: result.roadmap.source_plan,
          horizon_weeks: result.roadmap.horizon_weeks,
          start_date: result.roadmap.start_date,
        });
    } catch (cause) {
      if (!controller.signal.aborted) setError(message(cause));
    } finally {
      if (current === revision.current) setBusy("");
    }
  }
  function change(field: keyof RoadmapInput, value: unknown) {
    setInput((current) => ({ ...current, [field]: value }));
    setFeedback("");
  }
  async function generate(event: FormEvent) {
    event.preventDefault();
    setBusy("generate");
    setError("");
    setFeedback("");
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      const result = await api.generateRoadmap(input, controller.signal);
      setDocument(result);
      setEditing(true);
      setDirty(true);
      setComposerOpen(false);
      setFeedback(
        "Your draft is ready. Review the milestones and tasks, then save your roadmap.",
      );
      requestAnimationFrame(() =>
        draftRef.current?.focus({ preventScroll: true }),
      );
    } catch (cause) {
      if (!controller.signal.aborted) setError(message(cause));
    } finally {
      setBusy("");
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!document) return;
    for (const [index, item] of document.milestones.entries()) {
      if (
        item.start_week > item.end_week ||
        item.end_week > document.horizon_weeks ||
        item.depends_on.some(
          (n) => document.milestones[n - 1]?.end_week > item.start_week,
        )
      ) {
        setError(
          `Check the schedule for milestone ${index + 1}. It must fit within the planning horizon and start after its dependencies finish.`,
        );
        return;
      }
      if (
        !item.deliverables.some((task) => task.trim()) ||
        item.deliverables.filter((task) => task.trim()).length > 8 ||
        item.deliverables.some((task) => task.length > 512)
      ) {
        setError(
          `Milestone ${index + 1} needs between one and eight tasks, with at most 512 characters per task.`,
        );
        return;
      }
    }
    setBusy("save");
    setError("");
    setFeedback("");
    try {
      const cleaned = {
        ...document,
        milestones: document.milestones.map((item) => ({
          ...item,
          deliverables: item.deliverables
            .filter((task) => task.trim())
            .map((task) => task.trim()),
        })),
      };
      const result = await api.saveRoadmap({
        project_id: input.project_id,
        name: input.name,
        expected_version: saved?.version ?? 0,
        roadmap: cleaned,
        create_tasks: createTasks,
      });
      setSaved(result);
      setDocument(result.roadmap);
      setInput((current) => ({
        ...current,
        project_id: result.project_id,
        name: result.name,
      }));
      setEditing(false);
      setDirty(false);
      setComposerOpen(false);
      setFeedback(
        `Roadmap saved.${result.created_task_count ? ` ${result.created_task_count} tasks created in your project.` : ""}`,
      );
      setProjects((current) =>
        current.some((p) => p.id === result.project_id)
          ? current.map((p) =>
              p.id === result.project_id ? { ...p, has_roadmap: true } : p,
            )
          : [
              ...current,
              {
                id: result.project_id,
                name: result.name,
                slug: "",
                description: null,
                status: "active",
                notion_page_id: null,
                linear_project_id: null,
                has_roadmap: true,
              },
            ],
      );
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy("");
    }
  }
  function milestone(index: number, patch: Partial<RoadmapMilestone>) {
    setDocument(
      (current) =>
        current && {
          ...current,
          milestones: current.milestones.map((item, i) =>
            i === index ? { ...item, ...patch } : item,
          ),
        },
    );
    setDirty(true);
  }
  function remove(index: number) {
    setDocument(
      (current) =>
        current && {
          ...current,
          milestones: current.milestones
            .filter((_, i) => i !== index)
            .map((item) => ({
              ...item,
              depends_on: item.depends_on
                .filter((n) => n !== index + 1)
                .map((n) => (n > index + 1 ? n - 1 : n)),
            })),
        },
    );
    setDirty(true);
  }
  const availableMeetings = meetings.filter(
    (meeting) =>
      meeting.status === "completed" &&
      (!input.project_id || meeting.project_id === input.project_id),
  );
  const completed =
    document?.milestones.filter((item) => item.status === "done").length ?? 0;
  const taskCount =
    document?.milestones.reduce(
      (sum, item) => sum + item.deliverables.length,
      0,
    ) ?? 0;
  return (
    <div className="workspace-page roadmap-workspace" aria-busy={!!busy}>
      <header className="page-heading">
        <div>
          <h2>
            A plan. A path. <em>What’s next.</em>
          </h2>
          <p>
            Turn a project idea or a meeting into milestones your team can move
            on.
          </p>
        </div>
        <Icon name="roadmap" />
      </header>
      <div className="roadmap-project-picker">
        <label htmlFor="roadmap-project">Project</label>
        <select
          id="roadmap-project"
          value={input.project_id ?? ""}
          disabled={!!busy || dirty}
          onChange={(event) => void chooseProject(event.target.value)}
        >
          <option value="">Create a new project</option>
          {projects.map((project) => (
            <option key={project.id} value={project.id}>
              {project.name}
              {project.has_roadmap ? " · Roadmap saved" : ""}
            </option>
          ))}
        </select>
        {dirty && <span>Save or discard your draft to switch projects.</span>}
      </div>
      {error && (
        <div className="load-error" role="alert">
          {error}
          <button
            type="button"
            className="text-button"
            onClick={() => void load()}
            disabled={!!busy}
          >
            Retry connection
          </button>
        </div>
      )}
      {feedback && (
        <p className="roadmap-feedback" role="status">
          <Icon name="check" />
          {feedback}
        </p>
      )}
      <details
        className="roadmap-composer"
        open={composerOpen}
        onToggle={(event) => setComposerOpen(event.currentTarget.open)}
      >
        <summary>
          {document ? "Plan a new version" : "Start with what you have"}
          <Icon name="down" />
        </summary>
        <form onSubmit={generate}>
          <fieldset disabled={!!busy}>
            {!input.project_id && (
              <label htmlFor="roadmap-name">
                Project name
                <input
                  id="roadmap-name"
                  value={input.name}
                  onChange={(event) => change("name", event.target.value)}
                  required
                  maxLength={256}
                  placeholder="What are you working toward?"
                />
              </label>
            )}
            <div className="roadmap-source-label">Build the roadmap from</div>
            <div
              className="roadmap-source-options"
              role="group"
              aria-label="Roadmap source"
            >
              {[
                { id: "manual", label: "Write a plan", icon: "summary" },
                {
                  id: "transcript",
                  label: "A meeting transcript",
                  icon: "meetings",
                },
                { id: "project", label: "Project meetings", icon: "roadmap" },
              ].map((source) => (
                <button
                  key={source.id}
                  type="button"
                  aria-pressed={input.source_type === source.id}
                  onClick={() => change("source_type", source.id)}
                  disabled={source.id === "project" && !input.project_id}
                >
                  <Icon name={source.icon as "summary"} />
                  {source.label}
                </button>
              ))}
            </div>
            {input.source_type === "transcript" && (
              <label htmlFor="roadmap-meeting">
                Meeting transcript
                <select
                  id="roadmap-meeting"
                  value={input.transcript_id ?? ""}
                  required
                  onChange={(event) =>
                    change("transcript_id", event.target.value || null)
                  }
                >
                  <option value="">Choose a completed meeting</option>
                  {availableMeetings.map((meeting) => (
                    <option key={meeting.id} value={meeting.id}>
                      {meeting.title}
                    </option>
                  ))}
                </select>
                {!availableMeetings.length && (
                  <small>
                    No completed meetings found
                    {input.project_id ? " for this project" : ""}. Upload a
                    transcript or write a plan.
                  </small>
                )}
                {meetings.length < meetingTotal && (
                  <button
                    className="text-button"
                    type="button"
                    onClick={async () => {
                      setBusy("load");
                      try {
                        const more = await api.transcripts({
                          limit: 100,
                          offset: meetings.length,
                        });
                        setMeetings((current) => [...current, ...more.items]);
                      } catch (cause) {
                        setError(message(cause));
                      } finally {
                        setBusy("");
                      }
                    }}
                  >
                    Load more meetings
                  </button>
                )}
              </label>
            )}
            {input.source_type === "project" && (
              <p className="roadmap-source-note">
                Uses up to five recent completed meetings from this project.
                Long transcripts are shortened for planning.
              </p>
            )}
            <label htmlFor="roadmap-plan">
              {input.source_type === "manual"
                ? "Your goal, milestones, and tasks"
                : "Additional goals or guidance (optional)"}
              <textarea
                id="roadmap-plan"
                value={input.plan}
                onChange={(event) => change("plan", event.target.value)}
                minLength={input.source_type === "manual" ? 20 : undefined}
                required={input.source_type === "manual"}
                maxLength={20000}
                rows={6}
                placeholder="Describe the goal, the milestones you want to achieve, and the tasks needed for each. Include owners and deadlines if you know them."
              />
            </label>
            <div className="roadmap-timing">
              <label htmlFor="roadmap-duration">
                Planning horizon (weeks)
                <input
                  id="roadmap-duration"
                  type="number"
                  min={1}
                  max={104}
                  required
                  value={input.horizon_weeks}
                  onChange={(event) =>
                    change("horizon_weeks", Number(event.target.value))
                  }
                />
              </label>
              <label htmlFor="roadmap-start">
                Start date (optional)
                <input
                  id="roadmap-start"
                  type="date"
                  value={input.start_date ?? ""}
                  onChange={(event) =>
                    change("start_date", event.target.value || null)
                  }
                />
              </label>
            </div>
            <div className="roadmap-composer-footer">
              <p>AI proposes the sequence. You decide what to commit to.</p>
              <button
                className="primary"
                disabled={!!busy || !input.name.trim()}
              >
                {busy === "generate"
                  ? "Building your roadmap…"
                  : "Generate roadmap"}
                <Icon name="arrow" />
              </button>
            </div>
          </fieldset>
        </form>
      </details>
      {!document && !busy && (
        <div className="roadmap-introduction">
          <Icon name="roadmap" />
          <div>
            <h3>Make the next steps visible.</h3>
            <p>
              Start with a plan or conversation. Get a proposed sequence of
              milestones, clear deliverables, and tasks you can track in the
              workspace.
            </p>
          </div>
        </div>
      )}
      {document && (
        <section
          ref={draftRef}
          className="roadmap-document"
          tabIndex={-1}
          aria-label="Project roadmap"
        >
          <div className="roadmap-document-heading">
            <div>
              <h3>{input.name}</h3>
              <p>
                {document.horizon_weeks} weeks · {completed} of{" "}
                {document.milestones.length} milestones complete · {taskCount}{" "}
                tasks
              </p>
            </div>
            <span className="roadmap-version">
              {dirty
                ? "Unsaved draft"
                : `Saved · version ${saved?.version ?? 1}`}
            </span>
          </div>
          <form onSubmit={save}>
            <fieldset disabled={!!busy}>
              {editing ? (
                <label htmlFor="roadmap-summary">
                  Roadmap overview
                  <textarea
                    id="roadmap-summary"
                    value={document.summary}
                    required
                    maxLength={3000}
                    onChange={(event) => {
                      setDocument({ ...document, summary: event.target.value });
                      setDirty(true);
                    }}
                  />
                </label>
              ) : (
                <p className="roadmap-summary">{document.summary}</p>
              )}
              {document.assumptions.length > 0 && (
                <details className="roadmap-assumptions">
                  <summary>
                    Assumptions & open questions ({document.assumptions.length})
                  </summary>
                  <ul>
                    {document.assumptions.map((assumption, index) => (
                      <li key={index}>{assumption}</li>
                    ))}
                  </ul>
                </details>
              )}
              <ol className="roadmap-timeline">
                {document.milestones.map((item, index) => (
                  <li key={index} className={`milestone-${item.status}`}>
                    <span className="roadmap-node" aria-hidden="true">
                      {item.status === "done" ? (
                        <Icon name="check" />
                      ) : (
                        index + 1
                      )}
                    </span>
                    <div className="roadmap-milestone">
                      <div className="roadmap-milestone-heading">
                        <div>
                          {editing ? (
                            <label htmlFor={`milestone-title-${index}`}>
                              Milestone {index + 1}
                              <input
                                id={`milestone-title-${index}`}
                                value={item.title}
                                required
                                maxLength={200}
                                onChange={(event) =>
                                  milestone(index, {
                                    title: event.target.value,
                                  })
                                }
                              />
                            </label>
                          ) : (
                            <h4>{item.title}</h4>
                          )}
                          <span className="roadmap-week">
                            Week {item.start_week}
                            {item.end_week !== item.start_week
                              ? `–${item.end_week}`
                              : ""}
                            {item.owner ? ` · ${item.owner}` : " · Unassigned"}
                          </span>
                        </div>
                        <label className="roadmap-status-label">
                          <span className="sr-only">
                            Status of {item.title}
                          </span>
                          <select
                            aria-label={`Status of ${item.title}`}
                            value={item.status}
                            onChange={(event) =>
                              milestone(index, {
                                status: event.target
                                  .value as RoadmapMilestone["status"],
                              })
                            }
                          >
                            <option value="planned">Planned</option>
                            <option value="in_progress">In progress</option>
                            <option value="blocked">Blocked</option>
                            <option value="done">Done</option>
                          </select>
                        </label>
                      </div>
                      {editing ? (
                        <>
                          <label htmlFor={`milestone-objective-${index}`}>
                            Outcome
                            <textarea
                              id={`milestone-objective-${index}`}
                              value={item.objective}
                              required
                              maxLength={1000}
                              onChange={(event) =>
                                milestone(index, {
                                  objective: event.target.value,
                                })
                              }
                            />
                          </label>
                          <div className="roadmap-milestone-fields">
                            <label htmlFor={`milestone-start-${index}`}>
                              Start week
                              <input
                                id={`milestone-start-${index}`}
                                type="number"
                                required
                                min={1}
                                max={document.horizon_weeks}
                                value={item.start_week}
                                onChange={(event) =>
                                  milestone(index, {
                                    start_week: Number(event.target.value),
                                  })
                                }
                              />
                            </label>
                            <label htmlFor={`milestone-end-${index}`}>
                              End week
                              <input
                                id={`milestone-end-${index}`}
                                type="number"
                                required
                                min={item.start_week}
                                max={document.horizon_weeks}
                                value={item.end_week}
                                onChange={(event) =>
                                  milestone(index, {
                                    end_week: Number(event.target.value),
                                  })
                                }
                              />
                            </label>
                            <label htmlFor={`milestone-owner-${index}`}>
                              Owner (optional)
                              <input
                                id={`milestone-owner-${index}`}
                                value={item.owner ?? ""}
                                maxLength={128}
                                onChange={(event) =>
                                  milestone(index, {
                                    owner: event.target.value || null,
                                  })
                                }
                              />
                            </label>
                          </div>
                          <label htmlFor={`milestone-tasks-${index}`}>
                            Tasks (one per line)
                            <textarea
                              id={`milestone-tasks-${index}`}
                              value={item.deliverables.join("\n")}
                              required
                              onChange={(event) =>
                                milestone(index, {
                                  deliverables: event.target.value.split("\n"),
                                  task_ids: [],
                                })
                              }
                            />
                            <small>
                              Up to eight tasks per milestone. Saved tasks stay
                              in your task list when a milestone changes.
                            </small>
                          </label>
                          {document.milestones.length > 1 && (
                            <button
                              className="text-button roadmap-remove"
                              type="button"
                              onClick={() => remove(index)}
                            >
                              Remove milestone {index + 1}
                            </button>
                          )}
                        </>
                      ) : (
                        <>
                          <p>{item.objective}</p>
                          <ul className="roadmap-deliverables">
                            {item.deliverables.map((task, i) => (
                              <li key={i}>
                                <Icon name="tasks" />
                                {task}
                                {item.task_ids?.[i] && (
                                  <span>In task list</span>
                                )}
                              </li>
                            ))}
                          </ul>
                        </>
                      )}
                      {item.depends_on.length > 0 && (
                        <p className="roadmap-dependency">
                          After{" "}
                          {item.depends_on
                            .map((n) => `milestone ${n}`)
                            .join(", ")}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
              {editing && document.milestones.length < 12 && (
                <button
                  className="ghost"
                  type="button"
                  onClick={() => {
                    setDocument({
                      ...document,
                      milestones: [
                        ...document.milestones,
                        {
                          title: "",
                          objective: "",
                          start_week: 1,
                          end_week: document.horizon_weeks,
                          owner: null,
                          deliverables: [""],
                          depends_on: [],
                          status: "planned",
                          task_ids: [],
                        },
                      ],
                    });
                    setDirty(true);
                  }}
                >
                  <Icon name="plus" />
                  Add milestone
                </button>
              )}
              <div className="roadmap-save-bar">
                <div>
                  <label className="roadmap-task-toggle">
                    <input
                      type="checkbox"
                      checked={createTasks}
                      onChange={(event) => setCreateTasks(event.target.checked)}
                    />
                    Create missing tasks in the workspace
                  </label>
                  <small>Tasks stay in AOI. Connectors are optional.</small>
                </div>
                <div>
                  {!editing && (
                    <button
                      className="ghost"
                      type="button"
                      onClick={() => setEditing(true)}
                    >
                      Edit milestones
                    </button>
                  )}
                  {dirty && (
                    <button
                      className="ghost"
                      type="button"
                      onClick={() => {
                        setDocument(saved?.roadmap ?? null);
                        setDirty(false);
                        setEditing(false);
                        setComposerOpen(!saved?.roadmap);
                        setFeedback("");
                      }}
                    >
                      Discard draft
                    </button>
                  )}
                  <button
                    className="primary"
                    disabled={!!busy || (!dirty && !editing)}
                  >
                    {busy === "save"
                      ? "Saving…"
                      : createTasks
                        ? "Save roadmap & tasks"
                        : "Save roadmap"}
                    <Icon name="check" />
                  </button>
                </div>
              </div>
            </fieldset>
          </form>
        </section>
      )}
    </div>
  );
}
