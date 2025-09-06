{
  pkgs ? import <nixpkgs> { }
}:

pkgs.mkShell {
  buildInputs = with pkgs; [
    (python3.withPackages (pp: with pp; [
      pyside6
      # pyside6-stubs # FIXME missing
      # nur.repos.milahu.python3.pkgs.pyside6-stubs
      tree-sitter
      # tree-sitter-languages # broken
      tree-sitter-language-pack
      pillow
      mypy # type checker
    ]))
  ];
}
