/**
 * Componente de toast reutilizável.
 *
 * Uso:
 *   showToast("Configurações salvas com sucesso!", { type: "success" });
 *   showToast("Não foi possível salvar.", { type: "error" });
 *   showToast("Enviando...", { type: "info", duration: 0 }); // duration 0 = não some sozinho
 *
 * showToast() retorna uma função que fecha o toast manualmente, útil para
 * toasts de duração indefinida (ex: "salvando...") que devem ser
 * substituídos por um toast de sucesso/erro assim que a operação terminar.
 */
(function () {
  if (window.showToast) return;

  const CONTAINER_ID = "toast-container";
  const ICONS = { success: "✓", error: "✕", info: "ℹ" };

  const style = document.createElement("style");
  style.textContent = `
    #${CONTAINER_ID} {
      position: fixed;
      top: 20px;
      right: 20px;
      z-index: 9999;
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-width: calc(100vw - 40px);
    }

    .toast {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      min-width: 260px;
      max-width: 380px;
      padding: 12px 14px;
      border-radius: 6px;
      font-size: 14px;
      line-height: 1.4;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
      opacity: 0;
      transform: translateX(24px);
      transition: opacity 0.2s ease, transform 0.2s ease;
    }

    .toast.toast-show {
      opacity: 1;
      transform: translateX(0);
    }

    .toast-success {
      background: #1a2a1a;
      border: 1px solid #448b44;
      color: #8eff8a;
    }

    .toast-error {
      background: #2a1a1a;
      border: 1px solid #8b4444;
      color: #ff8a8a;
    }

    .toast-info {
      background: #1a2233;
      border: 1px solid #4a6a9e;
      color: #8ab4ff;
    }

    .toast-icon {
      flex-shrink: 0;
      font-weight: bold;
    }

    .toast-message {
      flex: 1;
      word-break: break-word;
    }

    .toast-close {
      flex-shrink: 0;
      background: none;
      border: none;
      color: inherit;
      cursor: pointer;
      font-size: 16px;
      line-height: 1;
      opacity: 0.6;
      padding: 0;
    }

    .toast-close:hover {
      opacity: 1;
    }

    @media (max-width: 640px) {
      #${CONTAINER_ID} {
        left: 20px;
        right: 20px;
      }

      .toast {
        max-width: none;
      }
    }
  `;
  document.head.appendChild(style);

  function getContainer() {
    let container = document.getElementById(CONTAINER_ID);
    if (!container) {
      container = document.createElement("div");
      container.id = CONTAINER_ID;
      container.setAttribute("aria-live", "polite");
      document.body.appendChild(container);
    }
    return container;
  }

  window.showToast = function showToast(message, options) {
    options = options || {};
    const type = ["success", "error", "info"].includes(options.type) ? options.type : "info";
    const duration = options.duration !== undefined ? options.duration : 4000;

    const container = getContainer();

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");

    const icon = document.createElement("span");
    icon.className = "toast-icon";
    icon.textContent = ICONS[type];

    const text = document.createElement("span");
    text.className = "toast-message";
    text.textContent = message;

    const closeBtn = document.createElement("button");
    closeBtn.type = "button";
    closeBtn.className = "toast-close";
    closeBtn.setAttribute("aria-label", "Fechar aviso");
    closeBtn.textContent = "×";

    toast.appendChild(icon);
    toast.appendChild(text);
    toast.appendChild(closeBtn);
    container.appendChild(toast);

    requestAnimationFrame(() => toast.classList.add("toast-show"));

    let removed = false;
    let timer = null;

    function remove() {
      if (removed) return;
      removed = true;
      if (timer) clearTimeout(timer);
      toast.classList.remove("toast-show");
      setTimeout(() => toast.remove(), 200);
    }

    closeBtn.addEventListener("click", remove);
    if (duration > 0) {
      timer = setTimeout(remove, duration);
    }

    return remove;
  };
})();
