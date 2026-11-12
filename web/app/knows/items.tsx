"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode, type Ref } from "react";
import type { InterviewFields, KnownInterview, KnownMemory } from "@/lib/knows-api";
import { COPY, interviewLine, interviewTitle, movedFrom, rolesFit, sourceLabel, type ProfileRow } from "@/lib/knows-view";
import { AREA, DANGER, FIELD, OUTLINE, PRIMARY, QUIET, QUIET_DANGER } from "@/lib/ui";

// One remembered item on the "What Wren knows" page: idle, being edited, or asking before a delete.
// The page owns which item is open and the one change on its way (lib/knows-form.ts).

export type Mode = "idle" | "edit" | "confirm";
export type Controls = {
  mode: Mode;
  busy: boolean;
  error: string | null;
  onEdit: () => void;
  onCancel: () => void;
  onAskDelete: () => void;
};

// The voice server's limits, so an edit can't be typed that would only ever fail (knows_routes.py).
const LIMIT = { memory: 1000, company: 200, role: 200, stage: 100, notes: 1000, name: 100, summary: 1000, roles: 1100 };

const clip = (text: string) => (text.length > 60 ? `${text.slice(0, 60)}…` : text);

// When an item closes (saved or cancelled), the keyboard comes back to its Edit button, unless focus has
// already gone somewhere else, such as another item's form.
function useFocusReturn(mode: Mode) {
  const edit = useRef<HTMLButtonElement>(null);
  const was = useRef(mode);
  useEffect(() => {
    const closed = was.current !== "idle" && mode === "idle";
    was.current = mode;
    if (closed && (document.activeElement === null || document.activeElement === document.body)) edit.current?.focus();
  }, [mode]);
  return edit;
}

function EditButton({ label, onClick, ref }: { label: string; onClick: () => void; ref: Ref<HTMLButtonElement> }) {
  return (
    <button ref={ref} type="button" aria-label={label} onClick={onClick} className={`${QUIET} -mt-2.5 shrink-0 text-[15px]`}>
      Edit
    </button>
  );
}

function Problem({ error }: { error: string | null }) {
  return error ? (
    <p role="alert" className="mt-2 text-[15px] text-brick">
      {error}
    </p>
  ) : null;
}

function Actions({ controls, canSave, remove }: { controls: Controls; canSave: boolean; remove: string | null }) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <button type="submit" disabled={controls.busy || !canSave} className={PRIMARY}>
        Save
      </button>
      <button type="button" onClick={controls.onCancel} disabled={controls.busy} className={OUTLINE}>
        Cancel
      </button>
      {remove ? (
        <button type="button" onClick={controls.onAskDelete} disabled={controls.busy} className={`${QUIET_DANGER} ml-auto text-[15px]`}>
          {remove}
        </button>
      ) : null}
    </div>
  );
}

function Confirm({ question, text, action, controls, onConfirm }: { question: string; text: string; action: string; controls: Controls; onConfirm: () => void }) {
  // The question takes focus, not the delete button, so Enter can't delete by accident.
  const asked = useRef<HTMLParagraphElement>(null);
  useEffect(() => asked.current?.focus(), []);
  return (
    <div>
      <p ref={asked} tabIndex={-1} className="text-[17px] font-bold outline-none">
        {question}
      </p>
      <p className="mt-1 text-[15px] leading-[1.4] break-words text-ink-soft">{text}</p>
      <Problem error={controls.error} />
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" onClick={onConfirm} disabled={controls.busy} className={DANGER}>
          {action}
        </button>
        <button type="button" onClick={controls.onCancel} disabled={controls.busy} className={OUTLINE}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function Idle({ children, edit }: { children: ReactNode; edit: ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <div className="min-w-0 flex-1">{children}</div>
      {edit}
    </div>
  );
}

function Label({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="mb-1 block text-[14px] font-[650]">
      {children}
    </label>
  );
}

const submitting = (save: () => void) => (event: FormEvent) => {
  event.preventDefault();
  save();
};

export function MemoryItem({ memory, tz, controls, onSave, onDelete }: { memory: KnownMemory; tz: string; controls: Controls; onSave: (text: string) => void; onDelete: () => void }) {
  const edit = useFocusReturn(controls.mode);
  const source = sourceLabel(memory.source, tz);
  if (controls.mode === "confirm") {
    return <Confirm question={COPY.askMemory} text={memory.text} action="Delete memory" controls={controls} onConfirm={onDelete} />;
  }
  if (controls.mode === "edit") return <MemoryEditor memory={memory} source={source} controls={controls} onSave={onSave} />;
  return (
    <Idle edit={<EditButton ref={edit} label={`Edit memory: ${clip(memory.text)}`} onClick={controls.onEdit} />}>
      {/* Exactly as stored: this is the text Wren uses. */}
      <p className="text-[17px] leading-[1.4] break-words">{memory.text}</p>
      <p className="mt-1.5 text-[14px] leading-[1.4] text-ink-soft">{source}</p>
    </Idle>
  );
}

function MemoryEditor({ memory, source, controls, onSave }: { memory: KnownMemory; source: string; controls: Controls; onSave: (text: string) => void }) {
  const [text, setText] = useState(memory.text);
  const id = `memory-${memory.id}`;
  return (
    <form onSubmit={submitting(() => text.trim() && onSave(text.trim()))}>
      <label htmlFor={id} className="sr-only">
        Memory
      </label>
      <textarea id={id} rows={3} autoFocus maxLength={LIMIT.memory} value={text} onChange={(event) => setText(event.target.value)} className={AREA} />
      <p className="mt-1.5 text-[14px] leading-[1.4] text-ink-soft">{source}</p>
      <Problem error={controls.error} />
      <Actions controls={controls} canSave={text.trim() !== ""} remove="Delete memory" />
    </form>
  );
}

export function InterviewItem({ interview, tz, controls, onSave, onDelete }: { interview: KnownInterview; tz: string; controls: Controls; onSave: (fields: InterviewFields) => void; onDelete: () => void }) {
  const edit = useFocusReturn(controls.mode);
  const title = interviewTitle(interview);
  if (controls.mode === "confirm") {
    return <Confirm question={COPY.askInterview} text={`${title}. ${interviewLine(interview)}`} action="Delete interview" controls={controls} onConfirm={onDelete} />;
  }
  if (controls.mode === "edit") return <InterviewEditor interview={interview} controls={controls} onSave={onSave} />;
  const moved = movedFrom(interview);
  return (
    <Idle edit={<EditButton ref={edit} label={`Edit ${title}`} onClick={controls.onEdit} />}>
      <p className="text-[17px] leading-[1.35] font-bold break-words">{title}</p>
      <p className="text-[17px] leading-[1.4] break-words">{interviewLine(interview)}</p>
      {moved ? (
        <p className="mt-1 text-[14px] leading-[1.4]">
          Moved from <s>{moved}</s>
        </p>
      ) : null}
      {interview.notes ? <p className="mt-1 text-[15px] leading-[1.4] break-words">{interview.notes}</p> : null}
      <p className="mt-1.5 text-[14px] leading-[1.4] text-ink-soft">{sourceLabel(interview.source, tz)}</p>
    </Idle>
  );
}

function InterviewEditor({ interview, controls, onSave }: { interview: KnownInterview; controls: Controls; onSave: (fields: InterviewFields) => void }) {
  const [company, setCompany] = useState(interview.company);
  const [role, setRole] = useState(interview.role ?? "");
  const [stage, setStage] = useState(interview.stage);
  const [date, setDate] = useState(interview.next_date ?? "");
  const [time, setTime] = useState(interview.next_time ?? "");
  const [notes, setNotes] = useState(interview.notes ?? "");
  const id = (field: string) => `interview-${interview.id}-${field}`;
  const canSave = company.trim() !== "" && stage.trim() !== "";
  // The row is corrected whole, so every field goes, with blanks as nothing.
  const save = () =>
    canSave &&
    onSave({
      company: company.trim(),
      role: role.trim() || null,
      stage: stage.trim(),
      next_date: date || null,
      next_time: time || null,
      notes: notes.trim() || null,
    });
  return (
    <form onSubmit={submitting(save)} className="grid gap-3">
      <div>
        <Label htmlFor={id("company")}>Company</Label>
        <input id={id("company")} required autoFocus maxLength={LIMIT.company} value={company} onChange={(e) => setCompany(e.target.value)} className={FIELD} />
      </div>
      <div>
        <Label htmlFor={id("role")}>Role</Label>
        <input id={id("role")} maxLength={LIMIT.role} value={role} onChange={(e) => setRole(e.target.value)} className={FIELD} />
      </div>
      <div>
        <Label htmlFor={id("stage")}>Stage</Label>
        <input id={id("stage")} required maxLength={LIMIT.stage} value={stage} onChange={(e) => setStage(e.target.value)} className={FIELD} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor={id("date")}>Date</Label>
          <input id={id("date")} type="date" value={date} onChange={(e) => setDate(e.target.value)} className={FIELD} />
        </div>
        <div>
          <Label htmlFor={id("time")}>Time</Label>
          <input id={id("time")} type="time" value={time} onChange={(e) => setTime(e.target.value)} className={FIELD} />
        </div>
      </div>
      <div>
        <Label htmlFor={id("notes")}>Notes</Label>
        <textarea id={id("notes")} rows={3} maxLength={LIMIT.notes} value={notes} onChange={(e) => setNotes(e.target.value)} className={AREA} />
      </div>
      <Problem error={controls.error} />
      <Actions controls={controls} canSave={canSave} remove="Delete interview" />
    </form>
  );
}

export function ProfileItem({ row, controls, onSave, onClear }: { row: ProfileRow; controls: Controls; onSave: (value: string) => void; onClear: () => void }) {
  const edit = useFocusReturn(controls.mode);
  if (controls.mode === "confirm") {
    return <Confirm question={COPY.askClear} text={row.value ?? ""} action="Clear" controls={controls} onConfirm={onClear} />;
  }
  if (controls.mode === "edit") return <ProfileEditor row={row} controls={controls} onSave={onSave} />;
  return (
    <Idle edit={row.editable ? <EditButton ref={edit} label={`Edit ${row.label}`} onClick={controls.onEdit} /> : null}>
      <p className="text-[17px] leading-[1.35] font-bold">{row.label}</p>
      <p className={`text-[17px] leading-[1.4] break-words ${row.value ? "" : "text-ink-soft"}`}>{row.value ?? COPY.notSet}</p>
    </Idle>
  );
}

function ProfileEditor({ row, controls, onSave }: { row: ProfileRow; controls: Controls; onSave: (value: string) => void }) {
  const [value, setValue] = useState(row.value ?? "");
  const id = `profile-${row.key}`;
  const roles = row.key === "target_roles";
  const canSave = !roles || rolesFit(value);
  const field =
    row.key === "summary" ? (
      <textarea id={id} rows={3} autoFocus maxLength={LIMIT.summary} value={value} onChange={(e) => setValue(e.target.value)} className={AREA} />
    ) : (
      <input
        id={id}
        autoFocus
        maxLength={roles ? LIMIT.roles : LIMIT.name}
        aria-describedby={roles ? `${id}-help` : undefined}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className={FIELD}
      />
    );
  return (
    <form onSubmit={submitting(() => canSave && onSave(value))}>
      <Label htmlFor={id}>{row.label}</Label>
      {field}
      {roles ? (
        <p id={`${id}-help`} className="mt-1.5 text-[14px] text-ink-soft">
          {COPY.rolesHelp}
        </p>
      ) : null}
      <Problem error={controls.error} />
      <Actions controls={controls} canSave={canSave} remove={row.value ? "Clear" : null} />
    </form>
  );
}
