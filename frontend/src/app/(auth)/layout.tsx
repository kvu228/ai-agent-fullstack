// Route group "(auth)": the folder name is not part of the URL (/login, /register).
// It only lets these pages share a layout different from the app's.
export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-zinc-200 p-6 shadow-sm dark:border-zinc-800">
        {children}
      </div>
    </main>
  );
}
