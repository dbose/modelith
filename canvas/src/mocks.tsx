import React from "react";
import ReactDOM from "react-dom/client";
import { MocksApp } from "./mocks/MocksApp";
import "./sme/sme.css";
import "./mocks/mocks.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MocksApp />
  </React.StrictMode>,
);
