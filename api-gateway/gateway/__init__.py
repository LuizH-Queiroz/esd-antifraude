"""API Gateway do Sistema Antifraude.

Ponto único de entrada/saída entre o Sistema Antifraude e qualquer sistema
externo (Sistema Bancário, Administrador). Nenhum outro microsserviço do
sistema é exposto diretamente de fora — tudo passa por aqui primeiro.

Por que o pacote se chama "gateway" e não "app" (como no simulador)?
Para evitar que os dois microsserviços definam pacotes Python de mesmo nome
("app"). Isso não causa problema dentro de containers isolados (cada
serviço roda em sua própria imagem Docker), mas evitaria confusão caso, no
futuro, alguém precise importar código de mais de um serviço no mesmo
processo Python (por exemplo, em testes de integração rodados localmente).
"""