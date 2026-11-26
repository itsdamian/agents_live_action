import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { FactoryProvider } from "./state/FactoryProvider";
import "./styles/tokens.css";
import "./styles/base.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root not found");

createRoot(root).render(
  <StrictMode>
    <FactoryProvider>
      <App />
    </FactoryProvider>
  </StrictMode>,
);
