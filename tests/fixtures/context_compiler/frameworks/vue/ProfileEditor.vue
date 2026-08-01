<template>
  <div>
    <ProfileCard />
    <profile-card />
  </div>
</template>
<script>
import ProfileCard from "./ProfileCard.vue";
export default {
  name: "ProfileEditor",
  components: { ProfileCard },
};
</script>
<style src="./ProfileEditor.css"></style>
