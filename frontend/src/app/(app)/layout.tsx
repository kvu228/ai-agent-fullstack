import { AppHeader } from "@/components/layout/AppHeader";
import { AuthProvider } from "@/lib/auth/AuthProvider";

// Layout for every logged-in page. proxy.ts has already redirected visitors
// without a session cookie to /login before we get here.
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <AuthProvider>
      <div className="flex h-dvh flex-col">
        <AppHeader />
        <div className="flex min-h-0 flex-1">{children}</div>
      </div>
    </AuthProvider>
  );
}
