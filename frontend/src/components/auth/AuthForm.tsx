"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useActionState } from "react";

import { login, register } from "@/lib/api/auth";
import { ApiError } from "@/lib/api/client";
import { safeRedirectPath } from "@/lib/auth/redirect";

interface FormState {
  error: string | null;
  email: string; // echoed back so the field isn't wiped after a failed attempt
}

const COPY = {
  login: {
    title: "Đăng nhập",
    submit: "Đăng nhập",
    switchText: "Chưa có tài khoản?",
    switchLink: { href: "/register", label: "Đăng ký" },
  },
  register: {
    title: "Tạo tài khoản",
    submit: "Đăng ký",
    switchText: "Đã có tài khoản?",
    switchLink: { href: "/login", label: "Đăng nhập" },
  },
} as const;

/**
 * One form for both login and register.
 *
 * Uses React 19 form actions: `useActionState` gives us the pending flag and the
 * result of the last submit, no manual `onSubmit` / `useState` juggling needed.
 */
export function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const copy = COPY[mode];

  const [state, formAction, isPending] = useActionState(
    async (_previous: FormState, formData: FormData): Promise<FormState> => {
      const email = String(formData.get("email") ?? "");
      const password = String(formData.get("password") ?? "");
      try {
        if (mode === "register") {
          const fullName = String(formData.get("full_name") ?? "").trim();
          await register({ email, password, full_name: fullName || null });
        }
        // Sets the httpOnly auth cookies. Nothing to store on our side.
        await login({ email, password });
      } catch (error) {
        const message = error instanceof ApiError ? error.message : "Không kết nối được máy chủ";
        return { error: message, email };
      }
      // Read `?next=` only now (not during render): no Suspense boundary needed.
      const next = new URLSearchParams(window.location.search).get("next");
      router.replace(safeRedirectPath(next));
      return { error: null, email };
    },
    { error: null, email: "" },
  );

  return (
    <form action={formAction} className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold">{copy.title}</h1>

      {mode === "register" && (
        <Field label="Họ tên (tuỳ chọn)" name="full_name" type="text" autoComplete="name" />
      )}
      <Field
        label="Email"
        name="email"
        type="email"
        autoComplete="email"
        defaultValue={state.email}
        required
      />
      <Field
        label="Mật khẩu"
        name="password"
        type="password"
        autoComplete={mode === "login" ? "current-password" : "new-password"}
        minLength={mode === "register" ? 8 : undefined}
        required
      />

      {state.error && (
        <p
          role="alert"
          className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300"
        >
          {state.error}
        </p>
      )}

      <button
        type="submit"
        disabled={isPending}
        className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {isPending ? "Đang xử lý…" : copy.submit}
      </button>

      <p className="text-center text-sm text-zinc-500">
        {copy.switchText}{" "}
        <Link href={copy.switchLink.href} className="text-blue-600 hover:underline">
          {copy.switchLink.label}
        </Link>
      </p>
    </form>
  );
}

function Field({ label, ...props }: { label: string } & React.ComponentProps<"input">) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      <input
        {...props}
        className="rounded-xl border border-zinc-300 bg-transparent px-3 py-2 outline-none focus:border-blue-500 dark:border-zinc-700"
      />
    </label>
  );
}
