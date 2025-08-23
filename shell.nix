{
  pkgs ? import <nixpkgs> { }
}:

pkgs.mkShell {
  buildInputs = with pkgs; [
    (python3.withPackages (pp: with pp; [
      pyside6
      tree-sitter
      # tree-sitter-languages # broken
      tree-sitter-language-pack
    ]))
  ];
}
