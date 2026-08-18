import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import RenderPage from "./render/RenderPage";
import "./index.css";

const router = createBrowserRouter([
  { path: "/", element: <App /> },
  { path: "/render/:runId/:orientation", element: <RenderPage /> },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
