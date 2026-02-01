const loginButton = document.getElementById("login-button");
if (loginButton) {
  loginButton.addEventListener("click", async () => {
    const accountSelect = document.getElementById("login-account");
    const accountId = accountSelect?.value;
    if (!accountId) {
      return;
    }

    loginButton.disabled = true;
    loginButton.textContent = "Login läuft…";
    await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId }),
    });
    loginButton.disabled = false;
    loginButton.textContent = "Login starten";
  });
}
