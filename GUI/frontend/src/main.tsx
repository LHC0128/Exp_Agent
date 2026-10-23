import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import { NavigationGuard } from "./components/NavigationGuard";
import "./styles.css";

const router = createBrowserRouter([
  { path: "*", element: <NavigationGuard><App /></NavigationGuard> },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><RouterProvider router={router} /></React.StrictMode>,
);
