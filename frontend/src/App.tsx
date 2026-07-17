import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./AuthContext";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";

function RutaProtegida({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return null;
  if (!me) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function RutaPublica({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return null;
  if (me) return <Navigate to="/panel" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Router>
      <AuthProvider>
        <Routes>
          <Route
            path="/"
            element={
              <RutaPublica>
                <Login />
              </RutaPublica>
            }
          />
          <Route
            path="/panel"
            element={
              <RutaProtegida>
                <Dashboard />
              </RutaProtegida>
            }
          />
        </Routes>
      </AuthProvider>
    </Router>
  );
}
