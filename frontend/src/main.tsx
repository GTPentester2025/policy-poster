import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import App from "./App";
import AdminScreen from "./screens/AdminScreen";
import RenderPage from "./render/RenderPage";
import "./index.css";

const router = createBrowserRouter([
  { path: "/", element: <App /> },
  { path: "/admin", element: <AdminScreen /> },
  { path: "/render/:runId/:orientation", element: <RenderPage /> },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
