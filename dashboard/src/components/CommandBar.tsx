import { ArrowUp, Mic, Paperclip } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";

/**
 * The "Ask IRA anything..." bar.
 *
 * Typing here is not a second, lesser input path: the text goes to IRA on the
 * `lk.chat` topic, whose default handler interrupts her and generates a reply.
 * So a typed sentence behaves exactly like a spoken one -- she cuts off
 * mid-word and answers out loud.
 */
export function CommandBar({
  onSend,
  disabled = false,
  micEnabled,
  onToggleMic,
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  micEnabled: boolean;
  onToggleMic: () => void;
}) {
  const [text, setText] = useState("");
  const canSend = text.trim().length > 0 && !disabled;

  const submit = () => {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  };

  return (
    <motion.form
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, delay: 0.5, ease: [0.22, 1, 0.36, 1] }}
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="panel mx-auto flex w-full max-w-[920px] items-center gap-2.5 rounded-full px-3 py-2"
    >
      <button
        type="button"
        onClick={onToggleMic}
        aria-label={micEnabled ? "Mute microphone" : "Unmute microphone"}
        aria-pressed={micEnabled}
        className="grid size-9 shrink-0 place-items-center rounded-full transition-colors hover:bg-white/[0.07]"
        style={{ color: micEnabled ? "#22D3EE" : "#5A7290" }}
      >
        <Mic size={16} strokeWidth={1.9} />
      </button>

      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={disabled ? "IRA is offline" : "Ask IRA anything..."}
        aria-label="Ask IRA anything"
        disabled={disabled}
        className="min-w-0 flex-1 bg-transparent text-[13.5px] text-text placeholder:text-mute focus:outline-none disabled:cursor-not-allowed"
      />

      <button
        type="button"
        aria-label="Attach a file"
        disabled
        // Present but inert: attachments need a file path IRA can actually
        // read, which is sub-project C's work. Visible and disabled is honest;
        // a button that silently does nothing is not.
        className="grid size-9 shrink-0 place-items-center rounded-full text-mute/50 transition-colors disabled:cursor-not-allowed"
      >
        <Paperclip size={16} strokeWidth={1.9} />
      </button>

      <motion.button
        type="submit"
        aria-label="Send"
        disabled={!canSend}
        whileTap={canSend ? { scale: 0.92 } : undefined}
        animate={{ opacity: canSend ? 1 : 0.4 }}
        className="grid size-9 shrink-0 place-items-center rounded-full text-deep disabled:cursor-not-allowed"
        style={{
          background: "linear-gradient(140deg,#67E8F9,#2E7BFF)",
          boxShadow: canSend ? "var(--glow-cyan-soft)" : "none",
        }}
      >
        <ArrowUp size={16} strokeWidth={2.4} />
      </motion.button>
    </motion.form>
  );
}
