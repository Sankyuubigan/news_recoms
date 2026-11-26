import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// Регистрируем SW относительно текущего пути — работает на GitHub Pages по любому подпути
if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    const swUrl = new URL("sw.js", window.location.href).toString();
    navigator.serviceWorker.register(swUrl).catch(() => undefined);
  });
}
