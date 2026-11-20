"""Prueba rápida contra TU Neo4j (usa .env). Crea 2 conceptos con prefijo TEST_, una relación, y los borra."""
import app as m

m.crear_driver()
m.escribir("MERGE (a:Concepto {id:'TEST_a'}) SET a.nombre='TEST A', a.descripcion='', a.categoria='Prueba' "
           "MERGE (b:Concepto {id:'TEST_b'}) SET b.nombre='TEST B', b.descripcion='', b.categoria='Prueba'")
m.escribir("MATCH (a:Concepto {id:'TEST_a'}),(b:Concepto {id:'TEST_b'}) MERGE (a)-[r:RELACIONADO_CON {tipo:'usa'}]->(b) "
           "ON CREATE SET r.origen_creacion='ia_aprobada'")
m.escribir("MATCH (a:Concepto {id:'TEST_a'})-[r]->(b) SET r.verificacion_estado='requiere_revision'")
print(m.consultar("MATCH (a:Concepto {id:'TEST_a'})-[r]->(b) RETURN r.verificacion_estado AS e, r.origen_creacion AS o"))
m.escribir("MATCH (n:Concepto) WHERE n.id STARTS WITH 'TEST_' DETACH DELETE n")
print("OK: sintaxis Cypher válida y datos de prueba borrados")
