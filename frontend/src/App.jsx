import { useState, useEffect } from "react";
import { getSession, clearSession } from "./api/client";
import Login from "./pages/Login";
import POSTerminal from "./pages/POSTerminal";
import BackOffice from "./pages/BackOffice";

export default function App() {
  const [page, setPage] = useState("login");

  useEffect(() => {
    const session = getSession();
    if (session) {
      // managers/admins go to back office by default; cashiers go to POS
      setPage(["manager","admin"].includes(session.role) ? "backoffice" : "pos");
    }
  }, []);

  const handleLogin = (data) => {
    setPage(["manager","admin"].includes(data.role) ? "backoffice" : "pos");
  };

  if (page === "login")      return <Login onLogin={handleLogin} />;
  if (page === "pos")        return <POSTerminal onNavigate={setPage} />;
  if (page === "backoffice") return <BackOffice onNavigate={setPage} />;
  return null;
}
