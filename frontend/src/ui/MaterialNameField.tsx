import { useId } from "react";
import "./material-name-field.css";

export function MaterialNameField({value,onChange,disabled=false,label="教材名稱"}:{value:string;onChange:(name:string)=>void;disabled?:boolean;label?:string}) {
  const errorId=useId();
  return <div className="material-name-field">
    <label><span>{label}</span><input aria-label={label} value={value} disabled={disabled} maxLength={180}
      aria-invalid={!value.trim()} aria-describedby={!value.trim()?errorId:undefined} onChange={event=>onChange(event.target.value)}/></label>
    {!value.trim() && <p id={errorId} className="form-error" role="alert">請填寫{label}。</p>}
  </div>;
}
