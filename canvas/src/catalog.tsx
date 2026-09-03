import React from "react";
import ReactDOM from "react-dom/client";
import { CatalogApp } from "./catalog/CatalogApp";
import "./catalog/catalog.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <CatalogApp />
  </React.StrictMode>,
);
