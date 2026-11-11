import { authConfigured, signOut } from "../auth";
import { useEffect, useRef, useState } from "react";
import { request } from "../api";
import type { Navigate, View } from "../navigation";
import { Icon } from "./Icon";
import {
  controlSnapshot,
  fillControl,
  isDictationTarget,
  performAction,
  waitForApp,
  type VoiceAction,
} from "./voice-control";
import "./voice.css";

type RecognitionResult = { isFinal: boolean; 0: { transcript: string } };
type Recognition = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  onresult: ((event: { results: ArrayLike<RecognitionResult> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition;
  webkitSpeechRecognition?: new () => Recognition;
};
const routes: Record<string, View> = {
  dashboard: "dashboard",
  home: "dashboard",
  overview: "dashboard",
  roadmap: "roadmaps",
  roadmaps: "roadmaps",
  task: "tasks",
  tasks: "tasks",
  meetings: "meetings",
  decisions: "decisions",
  intelligence: "intelligence",
  "weekly summary": "weekly-summary",
  settings: "settings",
  "browser extension": "extension",
  "recording apps": "extension",
  "desktop recorder": "extension",
  pricing: "pricing",
  "upload transcript": "upload",
};

export function VoiceAssistant({
  view,
  onNavigate,
}: {
  view: View;
  onNavigate: Navigate;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [listening, setListening] = useState(false);
  const [working, setWorking] = useState(false);
  const [mode, setMode] = useState<"command" | "dictate">("command");
  const [notice, setNotice] = useState(
    "Tell AOI what you want to do, or dictate into a selected field.",
  );
  const [error, setError] = useState("");
  const [speak, setSpeak] = useState(false);
  const [language, setLanguage] = useState(navigator.language || "en-US");
  const recognition = useRef<Recognition | null>(null);
  const running = useRef<AbortController | null>(null);
  const focused = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const commandField = useRef<HTMLTextAreaElement>(null);
  const wasOpen = useRef(false);
  const transcript = useRef("");
  const currentView = useRef(view);
  currentView.current = view;
  const speakRef = useRef(speak);
  speakRef.current = speak;
  const supported = !!(
    (window as SpeechWindow).SpeechRecognition ||
    (window as SpeechWindow).webkitSpeechRecognition
  );
  function closePanel() {
    if (recognition.current) {
      recognition.current.onend = null;
      recognition.current.abort();
    }
    running.current?.abort();
    setOpen(false);
    setListening(false);
  }
  useEffect(() => {
    if (open) commandField.current?.focus({ preventScroll: true });
    else if (wasOpen.current) launcher.current?.focus({ preventScroll: true });
    wasOpen.current = open;
  }, [open]);
  useEffect(() => {
    const track = (event: FocusEvent) => {
      if (
        isDictationTarget(event.target as Element) &&
        !(event.target as Element).closest(".voice-panel")
      )
        focused.current = event.target as
          HTMLInputElement | HTMLTextAreaElement;
    };
    document.addEventListener("focusin", track);
    return () => {
      document.removeEventListener("focusin", track);
      if (recognition.current) {
        recognition.current.onend = null;
        recognition.current.abort();
      }
      running.current?.abort();
    };
  }, []);
  function reply(value: string) {
    setNotice(value);
    if (speakRef.current && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
      const speech = new SpeechSynthesisUtterance(value);
      speech.lang = language;
      window.speechSynthesis.speak(speech);
    }
  }
  async function execute(utterance: string) {
    if (!utterance.trim() || working) return;
    setError("");
    setWorking(true);
    const controller = new AbortController();
    running.current = controller;
    try {
      const simple = utterance.trim().toLowerCase().replace(/[.!?]+$/g, "");
      if (/^(?:please )?(?:log out|logout|sign out)(?: of (?:aoi|my account))?$/.test(simple)) {
        if (!authConfigured) { reply("This local workspace has no account session to log out of."); return; }
        try { await signOut(); }
        catch { throw new Error("This action could not log out. Check your connection and try again."); }
        return;
      }
      if (/^(?:please )?(?:open|show) (?:my |the )?account$/.test(simple)) {
        document.querySelector<HTMLButtonElement>(".account-trigger")?.click();
        reply("Opened your account controls.");
        return;
      }
      const match = utterance
        .trim()
        .toLowerCase()
        .replace(/[.!?]+$/g, "")
        .match(/^(?:please )?(?:go to|open|show|take me to) (?:the )?(.+)$/);
      if (match && routes[match[1]]) {
        onNavigate(routes[match[1]]);
        reply(`Opened ${match[1]}.`);
        return;
      }
      const history: string[] = [];
      for (let step = 0; step < 12; step++) {
        if (controller.signal.aborted) break;
        const snapshot = controlSnapshot();
        const plan = await request<{
          message: string;
          actions: VoiceAction[];
          done: boolean;
        }>("/api/assistant/command", {
          method: "POST",
          signal: controller.signal,
          body: JSON.stringify({
            utterance,
            view: currentView.current,
            page_text: snapshot.page_text,
            controls: snapshot.controls,
            history,
            local_date: new Intl.DateTimeFormat("en-CA").format(new Date()),
          }),
        });
        setNotice(plan.message);
        if (!plan.actions.length) {
          reply(plan.message);
          return;
        }
        for (const action of plan.actions) {
          if (controller.signal.aborted) break;
          history.push(performAction(action, snapshot.elements, onNavigate));
          await new Promise((resolve) => setTimeout(resolve, 80));
        }
        await waitForApp(controller.signal);
        // Always inspect the resulting screen before declaring a mutating command complete.
      }
      if (!controller.signal.aborted)
        reply(
          "I’ve reached the command step limit. Check the current screen, then tell me what to do next.",
        );
    } catch (cause) {
      if (!controller.signal.aborted) {
        let detail =
          "The command couldn’t finish. Check the connection and the current screen before trying again.";
        if (cause instanceof Error) {
          try {
            const value = JSON.parse(
              cause.message.slice(cause.message.indexOf("{")),
            ).detail;
            if (typeof value === "string") detail = value;
          } catch {
            detail =
              cause.message.startsWith("The screen") ||
              cause.message.startsWith("This action")
                ? cause.message
                : detail;
          }
        }
        setError(detail);
      }
    } finally {
      setWorking(false);
      running.current = null;
    }
  }
  function start() {
    setError("");
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    if (
      mode === "dictate" &&
      (!focused.current?.isConnected || !isDictationTarget(focused.current))
    ) {
      setError(
        "Select a text field in the app first, then choose Start listening.",
      );
      return;
    }
    const Constructor =
      (window as SpeechWindow).SpeechRecognition ||
      (window as SpeechWindow).webkitSpeechRecognition;
    if (!Constructor) return;
    const recognizer = new Constructor();
    recognition.current = recognizer;
    transcript.current = "";
    setText("");
    recognizer.continuous = true;
    recognizer.interimResults = true;
    recognizer.lang = language;
    let failed = false;
    recognizer.onresult = (event) => {
      let final = "",
        interim = "";
      for (const result of Array.from(event.results)) {
        if (result.isFinal) final += result[0].transcript + " ";
        else interim += result[0].transcript;
      }
      transcript.current = final.trim() || interim.trim();
      setText((final + interim).trim());
    };
    recognizer.onerror = (event) => {
      failed = true;
      setListening(false);
      setError(
        event.error === "not-allowed"
          ? "Microphone access was denied. Allow microphone access in your browser or type your command."
          : event.error === "no-speech"
            ? "No speech was heard. Try again or type your command."
            : "Speech recognition stopped. Check your microphone and connection, or type your command.",
      );
    };
    recognizer.onend = () => {
      setListening(false);
      recognition.current = null;
      const value = transcript.current;
      if (failed || !value) return;
      if (mode === "dictate") {
        const target = focused.current;
        if (!target?.isConnected || !isDictationTarget(target)) {
          setError("The selected field is no longer available.");
          return;
        }
        fillControl(
          target,
          `${target.value}${target.value && !/\s$/.test(target.value) ? " " : ""}${value}`,
        );
        reply("Added your words to the selected field.");
      } else void execute(value);
    };
    try {
      recognizer.start();
      setListening(true);
      setNotice(
        mode === "dictate"
          ? "Listening for your dictation. Choose Finish when you’re done."
          : "Listening. Tell me the goal, milestones, and tasks, then choose Finish & run.",
      );
    } catch {
      setError(
        "Couldn’t start the microphone. Try again or type your command.",
      );
    }
  }
  return (
    <div className="voice-assistant">
      {open && (
        <section
          id="voice-panel"
          className="voice-panel"
          aria-label="AOI voice assistant"
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              closePanel();
            }
          }}
        >
          <div className="voice-panel-heading">
            <div>
              <Icon name="mic" />
              <h2>Talk to AOI</h2>
            </div>
            <button
              className="icon-action"
              aria-label="Close voice assistant"
              onClick={closePanel}
            >
              <Icon name="close" />
            </button>
          </div>
          <p className="voice-description">
            “Go to Roadmaps. Create this goal, these milestones, and these
            tasks.”
          </p>
          <div className="voice-modes" role="group" aria-label="Speech mode">
            <button
              aria-pressed={mode === "command"}
              disabled={listening || working}
              onClick={() => setMode("command")}
            >
              Do an action
            </button>
            <button
              aria-pressed={mode === "dictate"}
              disabled={listening || working}
              onClick={() => setMode("dictate")}
            >
              Dictate into a field
            </button>
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void execute(text);
            }}
          >
            <label htmlFor="voice-command">
              {mode === "command" ? "Your command" : "Recognized words"}
            </label>
            <textarea
              id="voice-command"
              ref={commandField}
              value={text}
              maxLength={20000}
              onChange={(event) => setText(event.target.value)}
              rows={4}
              placeholder="Tell me what you want to do…"
              disabled={listening || working}
            />
            <div className="voice-command-actions">
              {supported && (
                <button
                  type="button"
                  className={listening ? "voice-listening" : "ghost"}
                  disabled={working}
                  onClick={() =>
                    listening ? recognition.current?.stop() : start()
                  }
                >
                  <Icon name="mic" />
                  {listening
                    ? mode === "command"
                      ? "Finish & run"
                      : "Finish dictation"
                    : "Start listening"}
                </button>
              )}
              {mode === "command" && (
                <button
                  className="primary"
                  disabled={listening || working || !text.trim()}
                >
                  {working ? "Working…" : "Run command"}
                  <Icon name="arrow" />
                </button>
              )}
              {working && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    running.current?.abort();
                    reply(
                      "Stopped. Completed changes stay saved; check the screen for any action still processing.",
                    );
                  }}
                >
                  Stop
                </button>
              )}
            </div>
          </form>
          {!supported && (
            <p className="voice-support">
              Speech recognition isn’t available in this browser. You can type
              commands here, or use a browser with speech recognition support.
            </p>
          )}
          <p className="voice-notice" role="status">
            {notice}
          </p>
          {error && (
            <p className="voice-error" role="alert">
              {error}
            </p>
          )}
          <details className="voice-preferences">
            <summary>Speech settings & privacy</summary>
            <label htmlFor="voice-language">
              Spoken language
              <select
                id="voice-language"
                value={language}
                disabled={listening}
                onChange={(event) => setLanguage(event.target.value)}
              >
                {[
                  ...new Set([
                    navigator.language,
                    "en-US",
                    "en-GB",
                    "es-ES",
                    "fr-FR",
                    "de-DE",
                    "hi-IN",
                    "ta-IN",
                    "te-IN",
                    "ja-JP",
                    "zh-CN",
                  ]),
                ].map((code) => (
                  <option key={code} value={code}>
                    {new Intl.DisplayNames([navigator.language], {
                      type: "language",
                    }).of(code) ?? code}
                  </option>
                ))}
              </select>
            </label>
            <label className="voice-read-aloud">
              <input
                type="checkbox"
                checked={speak}
                onChange={(event) => {
                  setSpeak(event.target.checked);
                  if (!event.target.checked) window.speechSynthesis?.cancel();
                }}
              />
              Read replies aloud
            </label>
            <p>
              Browser speech services may process microphone audio. Commands and
              the current app screen are processed by your workspace’s AI
              provider. Recording starts only when you choose Start listening.
            </p>
          </details>
        </section>
      )}
      <button
        ref={launcher}
        className={`voice-launcher${listening ? " is-listening" : ""}`}
        aria-expanded={open}
        aria-controls="voice-panel"
        aria-label={open ? "Hide voice assistant" : "Talk to AOI"}
        onClick={() => (open ? closePanel() : setOpen(true))}
      >
        <Icon name="mic" />
        <span>
          {listening
            ? "Listening…"
            : working
              ? "AOI is working…"
              : "Talk to AOI"}
        </span>
      </button>
    </div>
  );
}
