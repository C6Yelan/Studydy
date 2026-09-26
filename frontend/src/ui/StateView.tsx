import { Icon, type IconName } from "./Icon";
import "./styles.css";

type StateTone = "loading" | "empty" | "failure";

export function StateView({
  action,
  description,
  icon = "warning",
  image,
  live = false,
  title,
  tone,
}: {
  action?: React.ReactNode;
  description: string;
  icon?: IconName;
  image?: string;
  live?: boolean;
  title: string;
  tone: StateTone;
}) {
  const isFailure = tone === "failure";
  return (
    <section
      aria-live={live && !isFailure ? "polite" : undefined}
      className={`state-view state-view--page is-${tone}`}
      role={isFailure ? "alert" : undefined}
    >
      {image ? <img className="state-view__image" src={image} alt="" /> : (
        <span className="state-view__icon"><Icon name={icon} size={26} /></span>
      )}
      {tone === "loading" && <span className="loading-ring" aria-hidden="true" />}
      <h1>{title}</h1>
      <p>{description}</p>
      {action && <div className="state-view__actions">{action}</div>}
    </section>
  );
}
