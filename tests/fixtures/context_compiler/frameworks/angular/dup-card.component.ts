import { Component } from "@angular/core";

/** Colliding selector with ProfileCardComponent — stays ambiguous. */
@Component({
  selector: "app-profile-card",
  template: "<aside>dup</aside>",
})
export class DupCardComponent {}
