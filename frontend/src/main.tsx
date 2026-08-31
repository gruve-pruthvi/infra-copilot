import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { setUnauthorizedHandler } from "./api/client";
import { useAuthStore } from "./stores/authStore";
import "./index.css";

// Bridge the API layer to the auth store without a circular import.
setUnauthorizedHandler(() => useAuthStore.getState().onUnauthorized());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
