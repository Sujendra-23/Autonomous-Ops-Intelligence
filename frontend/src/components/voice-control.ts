import type { View } from "../navigation";

export type VoiceControl = {
  id: string;
  role: string;
  label: string;
  value: string;
  disabled: boolean;
  options: { label: string; value: string }[];
};
export type VoiceAction = {
  kind: "navigate" | "click" | "fill" | "select";
  target: string;
  value: string;
};
const sensitive =
  /password|api.?key|secret|token|credential|company-code|company-email/i;
export function isDictationTarget(
  element: Element | null,
): element is HTMLInputElement | HTMLTextAreaElement {
  if (!(
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement
  ))
    return false;
  const inputType =
    element instanceof HTMLInputElement ? element.type : "textarea";
  return (
    ["text", "textarea", "search", "email"].includes(inputType) &&
    !element.disabled &&
    !element.readOnly &&
    !sensitive.test(
      `${element.id} ${element.name} ${element.getAttribute("aria-label") ?? ""}`,
    )
  );
}
export function fillControl(
  element: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement,
  value: string,
) {
  const prototype =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : element instanceof HTMLSelectElement
        ? HTMLSelectElement.prototype
        : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")?.set?.call(
    element,
    value,
  );
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}
export function controlSnapshot() {
  const elements = new Map<string, HTMLElement>();
  const controls: VoiceControl[] = [];
  for (const element of document.querySelectorAll<HTMLElement>(
    "#workspace-main button, #workspace-main input, #workspace-main textarea, #workspace-main select, #workspace-main summary",
  )) {
    if (controls.length >= 200) break;
    if (!element.getClientRects().length || element.closest("[inert]"))
      continue;
    const field =
      element instanceof HTMLInputElement ||
      element instanceof HTMLTextAreaElement ||
      element instanceof HTMLSelectElement
        ? element
        : null;
    const type = element instanceof HTMLInputElement ? element.type : "";
    if (
      ["password", "hidden", "file"].includes(type) ||
      sensitive.test(
        `${element.id} ${field?.name ?? ""} ${element.getAttribute("aria-label") ?? ""}`,
      )
    )
      continue;
    const label =
      element.getAttribute("aria-label") ||
      (field?.labels
        ? [...field.labels].map((label) => label.textContent?.trim()).join(" ")
        : "") ||
      element.textContent?.trim() ||
      element.getAttribute("placeholder") ||
      "";
    if (!label) continue;
    const context =
      element
        .closest("tr, .roadmap-milestone, .task-editor")
        ?.textContent?.replace(/\s+/g, " ")
        .trim()
        .slice(0, 180) ?? "";
    const id = `c${controls.length}`;
    const role =
      element instanceof HTMLSelectElement
        ? "select"
        : element instanceof HTMLTextAreaElement
          ? "textarea"
          : type ||
            (element instanceof HTMLButtonElement ? "button" : "summary");
    const value =
      type === "checkbox"
        ? String((element as HTMLInputElement).checked)
        : (field?.value ?? "");
    controls.push({
      id,
      role,
      label: `${label}${context ? ` [${context}]` : ""}`.slice(0, 400),
      value: value.slice(0, 2000),
      disabled: element.matches(":disabled"),
      options:
        element instanceof HTMLSelectElement
          ? [...element.options]
              .slice(0, 110)
              .map((option) => ({ label: option.text, value: option.value }))
          : [],
    });
    elements.set(id, element);
  }
  return {
    controls,
    elements,
    page_text:
      document.getElementById("workspace-main")?.innerText.slice(0, 12000) ??
      "",
  };
}
export function performAction(
  action: VoiceAction,
  elements: Map<string, HTMLElement>,
  navigate: (view: View) => void,
) {
  if (action.kind === "navigate") {
    navigate(action.target as View);
    return `Opened ${action.target}`;
  }
  const element = elements.get(action.target);
  if (
    !element?.isConnected ||
    !element.getClientRects().length ||
    element.matches(":disabled")
  )
    throw new Error("The screen changed. Please repeat the command.");
  if (action.kind === "click") {
    element.click();
    return `Clicked ${element.getAttribute("aria-label") || element.textContent?.trim().slice(0, 100) || element.id}`;
  }
  if (!(
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLSelectElement
  ))
    throw new Error("That control cannot be edited by voice.");
  if (action.kind === "select" && !(element instanceof HTMLSelectElement))
    throw new Error("That control is not a selector.");
  if (
    action.kind === "fill" &&
    !["text", "search", "email", "textarea", "date", "time", "number"].includes(
      element instanceof HTMLInputElement
        ? element.type
        : element instanceof HTMLTextAreaElement
          ? "textarea"
          : "select",
    )
  )
    throw new Error("This field cannot be dictated.");
  if (sensitive.test(`${element.id} ${element.name}`))
    throw new Error("Enter credentials directly using the form.");
  fillControl(element, action.value);
  element.focus({ preventScroll: true });
  return `Set ${element.id || action.target} to ${action.value.slice(0, 500)}`;
}
export async function waitForApp(signal: AbortSignal) {
  const started = Date.now();
  // Let React commit the action before checking for async work.
  await new Promise((resolve) => setTimeout(resolve, 180));
  while (
    document.querySelector(
      '#workspace-main [aria-busy="true"], #workspace-main button[type="submit"]:disabled, #workspace-main button.primary:disabled',
    ) &&
    Date.now() - started < 125000
  ) {
    if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
    // An idle disabled Save button is not a pending operation.
    const busy = document.querySelector('#workspace-main [aria-busy="true"]');
    const processing = [
      ...document.querySelectorAll("#workspace-main button:disabled"),
    ].some((e) =>
      /saving|building|creating|processing|loading/i.test(e.textContent ?? ""),
    );
    if (!busy && !processing) break;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  if (signal.aborted) throw new DOMException("Cancelled", "AbortError");
  if (Date.now() - started >= 125000)
    throw new Error(
      "This action is still processing. Check the screen before trying again.",
    );
}
