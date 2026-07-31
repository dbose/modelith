import React from "react";
import ReactDOM from "react-dom/client";
import { SmeApp } from "./sme/SmeApp";
import "./sme/sme.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <SmeApp />
  </React.StrictMode>,
);
