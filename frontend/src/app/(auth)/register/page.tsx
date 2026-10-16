import type { Metadata } from "next";

import { AuthForm } from "@/components/auth/AuthForm";

export const metadata: Metadata = { title: "Đăng ký · Document Assistant" };

export default function RegisterPage() {
  return <AuthForm mode="register" />;
}
