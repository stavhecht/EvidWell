/**
 * Console login.
 *
 * The server returns one identical 401 for unknown email and wrong password,
 * and this form shows that message verbatim — narrowing it to "no such user"
 * would hand back the account enumeration the API deliberately avoids.
 *
 * The standfirst says what signing in commits you to rather than welcoming you:
 * every approval is recorded against a name, and this is the screen where a
 * reviewer takes that on.
 */

import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "./auth";
import {
  FIELD_LABEL,
  LOGIN_COLUMN,
  LOGIN_ERROR,
  LOGIN_FIELD,
  LOGIN_FIELD_GROUP,
  LOGIN_FORM,
  LOGIN_INTRO,
  LOGIN_PAGE,
  LOGIN_STANDFIRST,
  LOGIN_SUBMIT,
  LOGIN_TITLE,
} from "./styles";

export function LoginForm() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const destination =
    (location.state as { from?: { pathname: string } } | null)?.from?.pathname ??
    "/console";

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setPending(true);
    try {
      await login(email, password);
      void navigate(destination, { replace: true });
    } catch {
      setError("Incorrect email or password.");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className={LOGIN_PAGE}>
      <div className={LOGIN_COLUMN}>
        <div className={LOGIN_INTRO}>
          <h1 className={LOGIN_TITLE}>Editorial console</h1>
          <p className={LOGIN_STANDFIRST}>
            Reviewer access only. Every approval is recorded against the name you sign
            in with, and nothing in the queue can publish without one.
          </p>
        </div>

        <form onSubmit={onSubmit} className={LOGIN_FORM}>
          <FieldLabel htmlFor="console-email">Work email</FieldLabel>
          <input
            id="console-email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
            autoComplete="username"
            placeholder="you@evidwell.com"
            className={LOGIN_FIELD}
          />

          <div className={LOGIN_FIELD_GROUP}>
            <FieldLabel htmlFor="console-password">Password</FieldLabel>
            <input
              id="console-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              autoComplete="current-password"
              className={LOGIN_FIELD}
            />
          </div>

          {error ? (
            <p role="alert" className={LOGIN_ERROR}>
              {error}
            </p>
          ) : null}

          <button type="submit" disabled={pending} className={LOGIN_SUBMIT}>
            {pending ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </main>
  );
}

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: string }) {
  return (
    <label htmlFor={htmlFor} className={FIELD_LABEL}>
      {children}
    </label>
  );
}

