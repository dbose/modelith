/**
 * A small multi-step input helper — QuickPick / InputBox steps with back/forward, a
 * step indicator, and cancel — ported and trimmed from the official VS Code extension
 * sample (microsoft/vscode-extension-samples, quickinput-sample/multiStepInput.ts).
 *
 * The extension had no wizard infrastructure; this is the reusable spine the live-database
 * reverse wizard (and any future multi-field flow) drives. Native QuickInput, so it feels
 * like the rest of VS Code — no webview.
 */

import {
  Disposable,
  QuickInput,
  QuickInputButton,
  QuickInputButtons,
  QuickPickItem,
  window,
} from "vscode";

class InputFlowAction {
  static back = new InputFlowAction();
  static cancel = new InputFlowAction();
  static resume = new InputFlowAction();
}

type InputStep = (input: MultiStepInput) => Thenable<InputStep | void>;

export interface QuickPickParameters<T extends QuickPickItem> {
  title: string;
  step: number;
  totalSteps: number;
  items: T[];
  activeItem?: T;
  placeholder: string;
  buttons?: QuickInputButton[];
  ignoreFocusOut?: boolean;
}

interface InputBoxParameters {
  title: string;
  step: number;
  totalSteps: number;
  value: string;
  prompt: string;
  password?: boolean;
  validate?: (value: string) => Promise<string | undefined>;
  buttons?: QuickInputButton[];
  placeholder?: string;
  ignoreFocusOut?: boolean;
}

export class MultiStepInput {
  static async run(start: InputStep): Promise<void> {
    const input = new MultiStepInput();
    return input.stepThrough(start);
  }

  private current?: QuickInput;
  private steps: InputStep[] = [];

  private async stepThrough(start: InputStep): Promise<void> {
    let step: InputStep | void = start;
    while (step) {
      this.steps.push(step);
      if (this.current) {
        this.current.enabled = false;
        this.current.busy = true;
      }
      try {
        step = await step(this);
      } catch (err) {
        if (err === InputFlowAction.back) {
          this.steps.pop();
          step = this.steps.pop();
        } else if (err === InputFlowAction.resume) {
          step = this.steps.pop();
        } else if (err === InputFlowAction.cancel) {
          step = undefined;
        } else {
          throw err;
        }
      }
    }
    if (this.current) this.current.dispose();
  }

  async showQuickPick<T extends QuickPickItem, P extends QuickPickParameters<T>>({
    title,
    step,
    totalSteps,
    items,
    activeItem,
    placeholder,
    buttons,
    ignoreFocusOut,
  }: P): Promise<T> {
    const disposables: Disposable[] = [];
    try {
      return await new Promise<T>((resolve, reject) => {
        const input = window.createQuickPick<T>();
        input.title = title;
        input.step = step;
        input.totalSteps = totalSteps;
        input.ignoreFocusOut = ignoreFocusOut ?? true;
        input.placeholder = placeholder;
        input.items = items;
        if (activeItem) input.activeItems = [activeItem];
        input.buttons = [
          ...(this.steps.length > 1 ? [QuickInputButtons.Back] : []),
          ...(buttons || []),
        ];
        disposables.push(
          input.onDidTriggerButton((item) => {
            if (item === QuickInputButtons.Back) reject(InputFlowAction.back);
          }),
          input.onDidChangeSelection((sel) => sel[0] && resolve(sel[0])),
          input.onDidHide(() => reject(InputFlowAction.cancel)),
        );
        if (this.current) this.current.dispose();
        this.current = input;
        this.current.show();
      });
    } finally {
      disposables.forEach((d) => d.dispose());
    }
  }

  async showInputBox<P extends InputBoxParameters>({
    title,
    step,
    totalSteps,
    value,
    prompt,
    password,
    validate,
    buttons,
    placeholder,
    ignoreFocusOut,
  }: P): Promise<string> {
    const disposables: Disposable[] = [];
    try {
      return await new Promise<string>((resolve, reject) => {
        const input = window.createInputBox();
        input.title = title;
        input.step = step;
        input.totalSteps = totalSteps;
        input.value = value || "";
        input.prompt = prompt;
        input.password = password ?? false;
        input.ignoreFocusOut = ignoreFocusOut ?? true;
        input.placeholder = placeholder;
        input.buttons = [
          ...(this.steps.length > 1 ? [QuickInputButtons.Back] : []),
          ...(buttons || []),
        ];
        let validating = validate ? validate("") : Promise.resolve(undefined);
        disposables.push(
          input.onDidTriggerButton((item) => {
            if (item === QuickInputButtons.Back) reject(InputFlowAction.back);
          }),
          input.onDidAccept(async () => {
            const val = input.value;
            input.enabled = false;
            input.busy = true;
            if (!(await (validate ? validate(val) : Promise.resolve(undefined)))) {
              resolve(val);
            }
            input.enabled = true;
            input.busy = false;
          }),
          input.onDidChangeValue(async (text) => {
            const current = validate ? validate(text) : Promise.resolve(undefined);
            validating = current;
            const msg = await current;
            if (current === validating) input.validationMessage = msg;
          }),
          input.onDidHide(() => reject(InputFlowAction.cancel)),
        );
        if (this.current) this.current.dispose();
        this.current = input;
        this.current.show();
      });
    } finally {
      disposables.forEach((d) => d.dispose());
    }
  }
}

export { InputFlowAction };
