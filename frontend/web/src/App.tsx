import { Routes, Route, Navigate } from 'react-router'
import { HarnessProvider } from './providers/HarnessProvider'
import Layout from './components/layout/Layout'
import DashboardPage from './pages/DashboardPage'
import ConversationPage from './pages/ConversationPage'
import SandboxesPage from './pages/SandboxesPage'
import TaskDetailPage from './pages/TaskDetailPage'
import SettingsPage from './pages/SettingsPage'

export default function App() {
  return (
    <HarnessProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/conversation" replace />} />
          <Route path="conversation" element={<ConversationPage />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="sandboxes" element={<SandboxesPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="tasks/:taskId" element={<TaskDetailPage />} />
        </Route>
      </Routes>
    </HarnessProvider>
  )
}
