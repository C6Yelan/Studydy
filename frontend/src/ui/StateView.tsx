import { Icon, type IconName } from "./Icon";
import "./styles.css";

type StateTone = "loading" | "empty" | "failure" | "success" | "insufficient";

export function StateView({
  action,
  description,
  icon = "warning",
  image,
  live = false,
  title,
  tone,
  variant = "page",
}: {
  action?: React.ReactNode;
  description: string;
  icon?: IconName;
  image?: string;
  live?: boolean;
  title: string;
  tone: StateTone;
  variant?: "page" | "embedded";
}) {
  const Heading = variant === "embedded" ? "h2" : "h1";
  const isFailure = tone === "failure";
  return (
    <section
      aria-live={live && !isFailure ? "polite" : undefined}
      className={`state-view state-view--${variant} is-${tone}`}
      role={isFailure ? "alert" : undefined}
    >
      {image ? <img className="state-view__image" src={image} alt="" /> : (
        <span className="state-view__icon"><Icon name={icon} size={26} /></span>
      )}
      {tone === "loading" && <span className="loading-ring" aria-hidden="true" />}
      <Heading>{title}</Heading>
      <p>{description}</p>
      {action && <div className="state-view__actions">{action}</div>}
    </section>
  );
}
