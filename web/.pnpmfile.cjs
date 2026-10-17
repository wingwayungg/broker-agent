// typescript-eslint rejects TS 7; give it a private TS 6 and leave tsc/esbuild on 7.
// Drop once typescript-eslint supports TS >=7.1 (typescript-eslint#10940).
const ts6 = "npm:typescript@^6.0.0";

module.exports = {
  hooks: {
    readPackage(pkg) {
      if (pkg.name === "typescript-eslint" || pkg.name?.startsWith("@typescript-eslint/")) {
        pkg.dependencies = { ...pkg.dependencies, typescript: ts6 };
        delete pkg.peerDependencies?.typescript;
      }
      return pkg;
    },
  },
};
