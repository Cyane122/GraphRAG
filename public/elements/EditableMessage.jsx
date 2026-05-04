import { useState } from "react";

export default function EditableMessage(allProps) {
  // Chainlit 버전마다 props 전달 구조가 다름
  const raw = allProps.content
    ?? allProps.props?.content
    ?? allProps.element?.props?.content
    ?? "";

  const [text, setText] = useState(raw);

  const handleConfirm = () => {
    window.sendPrompt(`__EDIT__:${text}`);
  };

  const handleCancel = () => {
    window.sendPrompt("__EDIT_CANCEL__");
  };

  return (
    <div style={{ width: "100%" }}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        autoFocus
        style={{
          width: "100%",
          minHeight: "260px",
          background: "transparent",
          border: "1px solid rgba(255,255,255,0.2)",
          color: "inherit",
          padding: "12px",
          borderRadius: "6px",
          fontFamily: "inherit",
          fontSize: "inherit",
          lineHeight: "1.7",
          resize: "vertical",
          boxSizing: "border-box",
          outline: "none",
        }}
        onFocus={(e) => { e.target.style.borderColor = "rgba(255,255,255,0.5)"; }}
        onBlur={(e)  => { e.target.style.borderColor = "rgba(255,255,255,0.2)"; }}
      />
      <div style={{ marginTop: "10px", display: "flex", gap: "8px" }}>
        <button onClick={handleConfirm} style={{ padding: "6px 18px", cursor: "pointer", borderRadius: "5px", border: "none", background: "#4a9eff", color: "white", fontSize: "13px", fontWeight: "600" }}>
          완료
        </button>
        <button onClick={handleCancel} style={{ padding: "6px 18px", cursor: "pointer", borderRadius: "5px", border: "1px solid rgba(255,255,255,0.25)", background: "transparent", color: "inherit", fontSize: "13px" }}>
          취소
        </button>
      </div>
    </div>
  );
}