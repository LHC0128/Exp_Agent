import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import { NavigationGuard } from "./components/NavigationGuard";
import { SetupProvider } from "./components/setups/SetupContext";
import "./styles.css";

const router = createBrowserRouter([
  { path: "*", element: <SetupProvider><NavigationGuard><App /></NavigationGuard></SetupProvider> },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><RouterProvider router={router} /></React.StrictMode>,
);
