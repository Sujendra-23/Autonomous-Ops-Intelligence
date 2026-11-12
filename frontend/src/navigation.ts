export type View = "dashboard" | "roadmaps" | "meetings" | "extension" | "weekly-summary" | "upload" | "tasks" | "decisions" | "intelligence" | "settings" | "pricing";
export type Navigate = (view: View, meetingId?: string) => void;

export function readRoute(): { view: View; meetingId?: string; roomId?:string } {
  const parts = window.location.pathname.split("/").filter(Boolean);
  const views: View[] = ["dashboard", "roadmaps", "meetings", "extension", "weekly-summary", "upload", "tasks", "decisions", "intelligence", "settings", "pricing"];
  const view = views.includes(parts[0] as View) ? parts[0] as View : "dashboard";
  return { view, meetingId: view === "meetings" && parts[1] !== "rooms" ? parts[1] : undefined, roomId: view === "meetings" && parts[1] === "rooms" ? parts[2] : undefined };
}
