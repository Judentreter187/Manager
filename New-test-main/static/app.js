const loginButton = document.getElementById("login-button");
const loginModal = document.getElementById("login-modal");
const loginForm = document.getElementById("login-modal-form");
const loginStatus = document.getElementById("login-status");
let loginPoller = null;

const setLoginStatus = (message) => {
  if (loginStatus) {
    loginStatus.textContent = message;
  }
};

const closeLoginModal = () => {
  if (!loginModal) {
    return;
  }
  loginModal.classList.add("hidden");
  loginModal.setAttribute("aria-hidden", "true");
};

const openLoginModal = () => {
  if (!loginModal) {
    return;
  }
  loginModal.classList.remove("hidden");
  loginModal.setAttribute("aria-hidden", "false");
};

const updateLoginButtonState = (isDisabled) => {
  if (!loginButton) {
    return;
  }
  loginButton.disabled = isDisabled;
  loginButton.textContent = isDisabled ? "Login läuft…" : "Login starten";
};

const stopLoginPolling = () => {
  if (loginPoller) {
    window.clearInterval(loginPoller);
    loginPoller = null;
  }
};

const startLoginPolling = (accountId) => {
  stopLoginPolling();
  loginPoller = window.setInterval(async () => {
    const response = await fetch(`/api/login-jobs/${accountId}`);
    if (!response.ok) {
      return;
    }
    const job = await response.json();
    if (job.status === "waiting_for_user") {
      setLoginStatus("Login-Fenster geöffnet. Bitte im iOS-Fenster einloggen.");
      return;
    }
    if (job.status === "running") {
      setLoginStatus("Login wird vorbereitet…");
      return;
    }
    if (job.status === "completed") {
      setLoginStatus("Login abgeschlossen. Account wird geladen…");
      stopLoginPolling();
      window.setTimeout(() => window.location.reload(), 800);
      return;
    }
    setLoginStatus("Login wurde beendet.");
    stopLoginPolling();
    updateLoginButtonState(false);
  }, 3000);
};

document.querySelectorAll("[data-close-modal]").forEach((element) => {
  element.addEventListener("click", closeLoginModal);
});

if (loginButton) {
  loginButton.addEventListener("click", () => {
    openLoginModal();
  });
}

if (loginForm) {
  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(loginForm);
    const payload = {
      proxy: formData.get("proxy"),
      ios_profile: formData.get("ios_profile"),
      label: formData.get("label"),
    };

    updateLoginButtonState(true);
    setLoginStatus("Login wird gestartet…");
    closeLoginModal();

    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      setLoginStatus("Login konnte nicht gestartet werden.");
      updateLoginButtonState(false);
      return;
    }

    const data = await response.json();
    setLoginStatus("Login wird vorbereitet…");
    startLoginPolling(data.account_id);
  });
}
