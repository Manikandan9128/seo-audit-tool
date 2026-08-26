import { Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import LoginPage from "./auth/LoginPage";
import ClientListPage from "./clients/ClientListPage";
import ClientDetailPage from "./clients/ClientDetailPage";
import SettingsPage from "./settings/SettingsPage";
import Layout from "./components/Layout";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/clients"
        element={
          <ProtectedRoute>
            <ClientListPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/clients/:clientId"
        element={
          <ProtectedRoute>
            <ClientDetailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/settings"
        element={
          <ProtectedRoute>
            <SettingsPage />
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/clients" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Layout>
        <AppRoutes />
      </Layout>
    </AuthProvider>
  );
}
